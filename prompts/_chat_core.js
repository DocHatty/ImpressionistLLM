const params = new URLSearchParams(location.search);
let currentModel = params.get('model') || 'openai/gpt-5.5';
const SYSTEM_PROMPTS = [
    {role:'system', content:'Formatting: respond in plain text only. Do not use markdown (no ###, **, backticks). Avoid em dashes; use simple hyphen bullets if needed.'},
    {role:'system', content:'Follow-ups: if the user asks a conversational question or a follow-up that is not providing a new PROCEDURE/INDICATION/REASON FOR EXAM, answer that question directly and briefly. Do not repeat the full structured template unless the user provides a new study/protocol indication block or explicitly asks for a full write-up again.'}
];
let messageHistory = [];
let isStreaming = false;
let abortController = null;
let pendingAttachments = [];
let userIsScrolling = false;
let scrollAtStreamStart = true;
let shouldSnapOnDone = false;
let streamTimer = null;
let streamingSid = null;
let streamingMsg = null;
let currentSid = null;
let streamSource = null;
let streamBuf = '';
let streamFlushScheduled = false;
let streamContentNode = null;
let inputResizeRAF = 0;
let scrollRAF = 0;
let tempRAF = 0;
let runNonce = 0;
let activeRunNonce = 0;
const MAX_HISTORY = 60;
const POLL_INTERVAL_MS = 50;
const SCROLL_NEAR_BOTTOM_PX = 60;
const INPUT_MAX_HEIGHT_PX = 150;
const TEXT_ATTACHMENT_MAX_CHARS = 12000;
const DEBUG_STREAM = false;

const $ = id => document.getElementById(id);
const chatContainer = $('chatContainer');
const emptyState = $('emptyState');
const inputBox = $('inputBox');
const btnSend = $('btnSend');
const settingsPanel = $('settingsPanel');
const modelSelect = $('modelSelect');
const temperature = $('temperature');
const tempValue = $('tempValue');
const maxTokens = $('maxTokens');
const currentModelDisplay = $('currentModel');
const status = $('status');
const attachments = $('attachments');
const fileInput = $('fileInput');
const btnAttach = $('btnAttach');
const btnSettings = $('btnSettings');
const btnClear = $('btnClear');
const plainText = $('plainText');
const reasoningEnabled = $('reasoningEnabled');
const reasoningEffort = $('reasoningEffort');
const reasoningExclude = $('reasoningExclude');
const reasoningControls = $('reasoningControls');
let availableModels = [];

const debugLog = DEBUG_STREAM ? (...args) => console.debug(...args) : () => {};
const debugWarn = DEBUG_STREAM ? (...args) => console.warn(...args) : () => {};
const debugError = DEBUG_STREAM ? (...args) => console.error(...args) : () => {};

function makeSessionId() {
    const rand = Math.random().toString(36).slice(2, 10);
    return `web_${Date.now()}_${rand}`;
}

function modelDisplayName(model) {
    return window.PromptUtils.modelDisplayName(model);
}

function setStatusIdle() {
    status.textContent = '';
    status.className = 'status';
}

function setStatusStreaming(text = 'Streaming...') {
    status.textContent = text;
    status.className = 'status streaming';
}

function modelSupportsReasoning(modelId) {
    const m = availableModels.find(x => x.id === modelId);
    return window.PromptUtils.modelSupportsParameter(m, 'reasoning');
}

function syncReasoningUI() {
    const knownModel = availableModels.some(x => x.id === currentModel);
    const supported = knownModel ? modelSupportsReasoning(currentModel) : false;
    if (reasoningControls) reasoningControls.style.display = supported ? '' : 'none';
    if (!supported) {
        reasoningEnabled.value = 'false';
        reasoningExclude.value = 'false';
        reasoningEffort.value = 'medium';
    }
}

function getReasoningRequest() {
    if (reasoningEnabled.value !== 'true' || !modelSupportsReasoning(currentModel)) return null;
    return {
        effort: reasoningEffort.value || 'medium',
        exclude: reasoningExclude.value === 'true'
    };
}

function getReasoningStatusLabel() {
    const r = getReasoningRequest();
    if (!r) return '';
    return ` - reasoning ${String(r.effort || 'medium').toUpperCase()}${r.exclude ? ' answer-only' : ' preview+answer'}`;
}

function setStatusError(text) {
    status.textContent = text;
    status.className = 'status error';
}

function setSendButtonIdle() {
    btnSend.textContent = 'Send';
    btnSend.className = 'btn-send';
}

function setSendButtonStop() {
    btnSend.textContent = 'Stop';
    btnSend.className = 'btn-send btn-stop';
}

function flushStreamBuffer() {
    if (streamBuf && streamContentNode) {
        streamContentNode.appendChild(document.createTextNode(sanitizeAssistantText(streamBuf)));
        streamBuf = '';
    }
}

function closeStreamSource(source = streamSource) {
    if (!source) return;
    source.onopen = null;
    source.onmessage = null;
    source.onerror = null;
    try { source.close(); } catch {}
    if (streamSource === source) streamSource = null;
}

function closeSettingsPanel() {
    settingsPanel.classList.remove('show');
    btnSettings.setAttribute('aria-expanded', 'false');
}

function toggleSettingsPanel() {
    const willShow = !settingsPanel.classList.contains('show');
    settingsPanel.classList.toggle('show', willShow);
    btnSettings.setAttribute('aria-expanded', willShow ? 'true' : 'false');
    if (willShow) loadModels();
}

function resetStreamingState() {
    // Flush any pending stream data before clearing state to prevent data loss
    // from error paths or the Clear button firing while a RAF is still pending.
    flushStreamBuffer();

    isStreaming = false;
    shouldSnapOnDone = false;
    userIsScrolling = false;
    scrollAtStreamStart = true;
    streamBuf = '';
    streamFlushScheduled = false;
    streamContentNode = null;
    streamingMsg = null;
    streamingSid = null;
    activeRunNonce = 0;

    if (streamTimer) {
        clearInterval(streamTimer);
        streamTimer = null;
    }
    closeStreamSource();
    try { abortController?.abort(); } catch {}
    abortController = null;
}

function applyInit(init) {
    if (!init) return;
    if (init.title) document.title = init.title;
    if (init.model) {
        currentModel = init.model;
        currentModelDisplay.textContent = modelDisplayName(currentModel);
    }
    if (init.system) messageHistory.push({role:'system', content:init.system});
    if (init.user) { addMessage('user', init.user); messageHistory.push({role:'user', content:init.user}); }
    if (init.assistant) { addMessage('assistant', init.assistant); messageHistory.push({role:'assistant', content:init.assistant}); }
    if (messageHistory.length > 0) emptyState.style.display = 'none';
    if (init.autorun && init.user && !init.assistant) {
        queueMicrotask(() => startSeededRun());
    }
}

async function bootstrap() {
    // Fast paint: set minimal UI immediately; load init payload in background.
    const title = params.get('title') || 'ImpressionistLLM Chat';
    if (title) document.title = title;
    currentModel = params.get('model') || currentModel;
    currentModelDisplay.textContent = modelDisplayName(currentModel);
    inputBox.focus();

    SYSTEM_PROMPTS.forEach(p => messageHistory.push(p));

    const sid = params.get('sid') || makeSessionId();
    currentSid = sid;

    if (params.get('sid')) try {
        fetch(`/api/chat/init?sid=${encodeURIComponent(sid)}`).then(async (r) => {
            if (!r.ok) return;
            const j = await r.json();
            if (!j || !j.ok) return;
            applyInit(j.data || null);
        });
    } catch {}

}

bootstrap();

function startStreamPolling(sid) {
    streamingSid = sid;
    if (streamTimer) clearInterval(streamTimer);
    streamingMsg = null;
    streamBuf = '';
    streamFlushScheduled = false;
    // Capture the nonce for this polling session
    const myNonce = activeRunNonce;
    // Poll faster (50ms) for smoother UI if SSE is unavailable
    streamTimer = setInterval(() => {
        // Check if we're still on the same run
        if (myNonce !== activeRunNonce) {
            clearInterval(streamTimer);
            streamTimer = null;
            return;
        }
        fetchStreamChunks();
    }, POLL_INTERVAL_MS);
}

function startStreamSSE(sid) {
    debugLog(`[startStreamSSE] Starting SSE for sid=${sid}, run=${activeRunNonce}`);
    streamingSid = sid;
    
    // Clear any polling timer
    if (streamTimer) { clearInterval(streamTimer); streamTimer = null; }
    
    // Force close any existing streamSource with handler cleanup.
    if (streamSource) {
        debugLog(`[startStreamSSE] Closing existing EventSource`);
        closeStreamSource();
    }
    
    streamingMsg = null;
    streamBuf = '';
    streamFlushScheduled = false;

    // Capture the nonce for this SSE connection so we can detect stale events.
    const myNonce = activeRunNonce;

    try {
        const source = new EventSource(`/api/chat/stream/events?sid=${encodeURIComponent(sid)}`);
        streamSource = source;
        
        // Add onopen handler to verify connection is subscribed
        source.onopen = () => {
            debugLog(`[startStreamSSE] EventSource opened for run=${myNonce}`);
        };
        
        source.onmessage = (evt) => {
            // Check if this event is stale (from a previous run)
            if (myNonce !== activeRunNonce) {
                debugLog(`[startStreamSSE] Ignoring stale message from run=${myNonce}, current=${activeRunNonce}`);
                return;
            }
            
            try {
                const j = JSON.parse(evt.data || '{}');
                const chunks = j.chunks || [];
                if (chunks.length) {
                    const firstChunk = chunks[0] || '';
                    const preview = firstChunk.substring(0, 20);
                    debugLog(`[startStreamSSE] Received ${chunks.length} chunks for run=${myNonce}, first chunk length=${firstChunk.length}, preview="${preview}"`);
                    appendStreamChunks(chunks.join(''));
                }
                if (j.done) {
                    debugLog(`[startStreamSSE] Stream done for run=${myNonce}`);
                    // Ignore stale done signals from a previous run
                    if (myNonce === activeRunNonce) {
                        try { finalizeServerStreamDone(); } catch (e) {
                            debugError(`[startStreamSSE] Error in finalizeServerStreamDone:`, e);
                        }
                    } else {
                        debugLog(`[startStreamSSE] Ignoring stale done signal from run=${myNonce}`);
                    }
                    closeStreamSource(source);
                    streamingSid = null;
                }
            } catch (e) {
                debugError(`[startStreamSSE] Error processing message:`, e);
            }
        };
        
        source.onerror = (err) => {
            debugError(`[startStreamSSE] EventSource error for run=${myNonce}:`, err);
            // Use polling if SSE fails (e.g., corporate proxy / older browser)
            closeStreamSource(source);
            // Only fall back to polling if this is still the active run
            if (streamingSid && myNonce === activeRunNonce) {
                debugLog(`[startStreamSSE] Falling back to polling for run=${myNonce}`);
                startStreamPolling(streamingSid);
            } else {
                debugLog(`[startStreamSSE] Not falling back to polling (stale run=${myNonce})`);
            }
        };
        
        debugLog(`[startStreamSSE] EventSource created for run=${myNonce}`);
    } catch (e) {
        debugError(`[startStreamSSE] Failed to create EventSource for run=${myNonce}:`, e);
        if (myNonce === activeRunNonce) {
            debugLog(`[startStreamSSE] Falling back to polling after error`);
            startStreamPolling(sid);
        }
    }
}

async function fetchStreamChunks() {
    if (!streamingSid) return;
    try {
        const r = await fetch(`/api/chat/stream?sid=${encodeURIComponent(streamingSid)}`);
        if (!r.ok) return;
        const j = await r.json();
        if (!j || !j.ok) return;
        const chunks = j.chunks || [];
        if (chunks.length) {
            appendStreamChunks(chunks.join(''));
        }
        if (j.done) {
            clearInterval(streamTimer);
            streamTimer = null;
            streamingSid = null;
            // CRITICAL: Finalize the stream to persist message in history
            try { finalizeServerStreamDone(); } catch (e) {
                debugError(`[fetchStreamChunks] Error in finalizeServerStreamDone:`, e);
            }
        }
    } catch {}
}

function appendStreamChunks(text) {
    if (DEBUG_STREAM) {
        const textLength = text ? text.length : 0;
        const preview = text ? text.substring(0, 20) : '';
        debugLog(`[appendStreamChunks] Called with text length=${textLength}, preview="${preview}"`);
    }
    
    if (!text) {
        if (DEBUG_STREAM) debugLog(`[appendStreamChunks] Text is empty/falsy, returning early`);
        return;
    }
    if (!streamingMsg) {
        streamingMsg = addMessage('assistant', '');
        streamContentNode = streamingMsg._contentEl;
        if (isStreaming) streamingMsg.id = 'streaming';
    }
    if (isStreaming) {
        try { removeThinking(); } catch {}
        if (status.textContent === 'Connecting...') status.textContent = 'Streaming...';
    }
    streamBuf += text;
    if (!streamFlushScheduled) {
        streamFlushScheduled = true;
        requestAnimationFrame(() => {
            streamFlushScheduled = false;

            // Log state before the flush check
            if (DEBUG_STREAM) {
                debugLog(`[RAF flush] streamBuf length=${streamBuf.length}, streamContentNode defined=${!!streamContentNode}`);
            }

            // Optimized append: use cached node + TextNode to avoid querySelector + layout thrashing
            const hadBuffer = Boolean(streamBuf);
            flushStreamBuffer();
            if (DEBUG_STREAM && hadBuffer && !streamContentNode) {
                debugLog(`[RAF flush] Skipped: streamBuf empty=${!streamBuf}, streamContentNode missing=${!streamContentNode}`);
            }

            // Fluid follow behavior: keep following only while the user has not
            // intentionally scrolled away from the bottom.
            if (!userIsScrolling && (scrollAtStreamStart || isNearBottom())) {
                chatContainer.scrollTop = chatContainer.scrollHeight;
            }
        });
    }
}

function finalizeServerStreamDone() {
    // Guard against being called when not streaming or from a stale run
    if (!isStreaming || activeRunNonce === 0) return;

    // CRITICAL: Remove thinking indicator if still present (handles empty responses)
    try { removeThinking(); } catch {}

    // FIX: Synchronously flush any pending streamBuf into the DOM BEFORE
    // nulling streamContentNode. When chunks + done arrive in the same SSE
    // event, appendStreamChunks() schedules a RAF that hasn't fired yet.
    // Without this flush, the RAF finds streamContentNode=null and silently
    // drops the entire response.
    flushStreamBuffer();

    const streamMsg = $('streaming');
    if (streamMsg) streamMsg.removeAttribute('id');

    // Persist assistant response into history for follow-ups.
    const msgDiv = streamingMsg || streamMsg;
    const content = msgDiv?._contentEl?.textContent || msgDiv?.querySelector?.('.message-content')?.textContent || '';
    if (content) {
        messageHistory.push({role:'assistant', content});
        trimHistory();
    }

    setStatusIdle();

    const snap = shouldSnapOnDone && !userIsScrolling && scrollAtStreamStart;
    isStreaming = false;
    shouldSnapOnDone = false;
    streamContentNode = null;
    streamingMsg = null;
    streamingSid = null;
    activeRunNonce = 0;
    setSendButtonIdle();
    abortController = null;

    if (snap) {
        chatContainer.scrollTop = chatContainer.scrollHeight;
        userIsScrolling = false;
    }
}

function isNearBottom() {
    const remaining = chatContainer.scrollHeight - chatContainer.scrollTop - chatContainer.clientHeight;
    return remaining <= SCROLL_NEAR_BOTTOM_PX;
}

function trimHistory() {
    // Keep system prompts (first N) + most recent turns to prevent unbounded memory growth.
    const sysCount = SYSTEM_PROMPTS.length;
    if (messageHistory.length > MAX_HISTORY) {
        messageHistory = [...messageHistory.slice(0, sysCount), ...messageHistory.slice(-(MAX_HISTORY - sysCount))];
    }
}

chatContainer.addEventListener('scroll', () => {
    if (scrollRAF) return;
    scrollRAF = requestAnimationFrame(() => {
        scrollRAF = 0;
        userIsScrolling = !isNearBottom();
    });
}, { passive: true });

function addMessage(role, content) {
    emptyState.style.display = 'none';
    const d = document.createElement('div');
    d.className = 'message ' + role;
    const t = new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
    const label = role === 'user' ? 'You' : role === 'assistant' ? 'Assistant' : 'System';
    d.innerHTML = '<div class="message-header"><span class="message-role">' + label + '</span><span>' + t + '</span></div><div class="message-content"></div>';
    const contentEl = d.querySelector('.message-content');
    contentEl.textContent = content;
    d._contentEl = contentEl;
    d.addEventListener('animationend', () => d.classList.add('settled'), { once: true });
    chatContainer.appendChild(d);
    if (!isStreaming || (!userIsScrolling && isNearBottom())) chatContainer.scrollTop = chatContainer.scrollHeight;
    return d;
}

function addThinking() {
    const d = document.createElement('div');
    d.className = 'message assistant';
    d.id = 'thinking';
    d.innerHTML = '<div class="message-header"><span class="message-role">Assistant</span></div><div class="thinking"><span></span><span></span><span></span></div>';
    chatContainer.appendChild(d);
    if (!userIsScrolling) chatContainer.scrollTop = chatContainer.scrollHeight;
}

function removeThinking() {
    const t = $('thinking');
    if (t) t.remove();
}

function sanitizeAssistantText(text) {
    if (!plainText.checked) return text;
    let s = text || '';
    s = s.replace(/\u2014|\u2013/g, '-');
    s = s.replace(/^#{1,6}\s+/gm, '');
    s = s.replace(/```[\s\S]*?```/g, (m) => m.replace(/```/g, ''));
    s = s.replace(/\*\*(.*?)\*\*/g, '$1');
    s = s.replace(/__(.*?)__/g, '$1');
    s = s.replace(/`([^`]+)`/g, '$1');
    s = s.replace(/^\s*[\*\u2022]\s+/gm, '- ');
    return s;
}

function renderAttachmentChips() {
    attachments.innerHTML = '';
    if (!pendingAttachments.length) {
        attachments.classList.remove('show');
        return;
    }
    attachments.classList.add('show');

    pendingAttachments.forEach((att) => {
        const chip = document.createElement('div');
        chip.className = 'chip';
        const label = document.createElement('span');
        label.textContent = att.name || (att.kind === 'image' ? 'Image' : 'Attachment');
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.title = 'Remove';
        btn.textContent = 'x';
        btn.onclick = () => {
            const i = pendingAttachments.indexOf(att);
            if (i >= 0) pendingAttachments.splice(i, 1);
            renderAttachmentChips();
        };
        chip.appendChild(label);
        chip.appendChild(btn);
        attachments.appendChild(chip);
    });
}

function addAttachmentsFromFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;

    let addedAny = false;
    for (const f of files) {
        if (f.type && f.type.startsWith('image/')) {
            const reader = new FileReader();
            reader.onload = () => {
                pendingAttachments.push({kind:'image', name:f.name, mime:f.type, dataUrl:String(reader.result || '')});
                renderAttachmentChips();
            };
            reader.readAsDataURL(f);
            addedAny = true;
            continue;
        }

        if ((f.type && f.type.startsWith('text/')) || /\.(txt|md|json|csv|log)$/i.test(f.name || '')) {
            const reader = new FileReader();
            reader.onload = () => {
                const raw = String(reader.result || '');
                const maxChars = TEXT_ATTACHMENT_MAX_CHARS;
                const clipped = raw.length > maxChars ? raw.slice(0, maxChars) + `\n\n[...clipped ${raw.length - maxChars} chars...]` : raw;
                pendingAttachments.push({kind:'text', name:f.name, text:clipped});
                renderAttachmentChips();
            };
            reader.readAsText(f);
            addedAny = true;
            continue;
        }

        setStatusError(`Unsupported file: ${f.name} (images + text files only)`);
    }

    if (addedAny) {
        setStatusIdle();
    }
}

function buildUserMessage(text) {
    let combined = text || '';
    const textFiles = pendingAttachments.filter(a => a.kind === 'text');
    for (const a of textFiles) {
        combined += `\n\n[Attachment: ${a.name}]\n${a.text}`;
    }

    const images = pendingAttachments.filter(a => a.kind === 'image');
    if (images.length) {
        const content = [{type:'text', text: combined || '(image attached)'}];
        for (const img of images) content.push({type:'image_url', image_url:{url: img.dataUrl}});
        return {role:'user', content};
    }

    return {role:'user', content: combined};
}


async function startSeededRun() {
    if (isStreaming || !currentSid) return;
    if (!messageHistory.some(m => m && m.role === 'user')) return;

    runNonce++;
    activeRunNonce = runNonce;

    try {
        if (abortController) abortController.abort();
    } catch {}
    abortController = null;

    isStreaming = true;
    scrollAtStreamStart = isNearBottom();
    shouldSnapOnDone = true;
    setSendButtonStop();
    setStatusStreaming('Connecting...' + getReasoningStatusLabel());
    abortController = new AbortController();
    addThinking();

    try {
        const myRunNonce = activeRunNonce;
        const res = await fetch('/api/chat/run', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                sid: currentSid,
                request: {
                    model: currentModel,
                    messages: messageHistory,
                    temperature: parseFloat(temperature.value),
                    max_completion_tokens: parseInt(maxTokens.value),
                    ...(getReasoningRequest() ? { reasoning: getReasoningRequest() } : {})
                }
            }),
            signal: abortController.signal
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        startStreamSSE(currentSid);
    } catch (e) {
        removeThinking();
        const streamMsg = $('streaming');
        if (streamMsg) streamMsg.removeAttribute('id');
        if (e.name === 'AbortError') {
            status.className = 'status';
            status.textContent = 'Stopped';
        } else {
            setStatusError('Error: ' + e.message);
        }
        resetStreamingState();
        setSendButtonIdle();
    }
}

async function sendMessage() {
    const text = inputBox.value.trim();
    if ((!text && pendingAttachments.length === 0) || isStreaming) return;

    inputBox.value = '';
    inputBox.style.height = 'auto';

    if (text) addMessage('user', text);
    else addMessage('user', '(attachments)');
    messageHistory.push(buildUserMessage(text));
    trimHistory();
    pendingAttachments = [];
    renderAttachmentChips();

    // CRITICAL: Increment run nonce FIRST to mark all pending events as stale
    // This invalidates any in-flight SSE events from previous runs
    runNonce++;
    activeRunNonce = runNonce;
    debugLog(`[sendMessage] Starting run ${activeRunNonce}`);

    // Explicit cleanup of previous abort controller BEFORE creating new one
    try {
        if (abortController) {
            debugLog(`[sendMessage] Aborting previous controller`);
            abortController.abort();
        }
    } catch (e) {
        debugWarn(`[sendMessage] Failed to abort previous controller:`, e);
    }
    abortController = null;

    isStreaming = true;
    scrollAtStreamStart = isNearBottom();
    userIsScrolling = !scrollAtStreamStart;
    shouldSnapOnDone = scrollAtStreamStart;
    setSendButtonStop();
    setStatusStreaming('Connecting...' + getReasoningStatusLabel());

    abortController = new AbortController();
    addThinking();

    try {
        if (!currentSid) throw new Error('Missing session id');

        const myRunNonce = activeRunNonce;  // capture before async gap

        const res = await fetch('/api/chat/run', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                sid: currentSid,
                request: {
                    model: currentModel,
                    messages: messageHistory,
                    temperature: parseFloat(temperature.value),
                    max_completion_tokens: parseInt(maxTokens.value),
                    ...(getReasoningRequest() ? { reasoning: getReasoningRequest() } : {})
                }
            }),
            signal: abortController.signal
        });

        if (!res.ok) throw new Error('HTTP ' + res.status);
        debugLog(`[sendMessage] Run ${myRunNonce} started successfully`);

        // Open SSE only after run acceptance to avoid attaching to a stale `done=true`
        // stream record from the previous run and prematurely closing this run.
        debugLog(`[sendMessage] Starting SSE stream for sid=${currentSid}, run=${activeRunNonce}`);
        startStreamSSE(currentSid);
        // Stream output arrives via SSE and will finalize the UI on `done`.

    } catch (e) {
        debugError(`[sendMessage] Run ${activeRunNonce} failed:`, e);
        removeThinking();
        const streamMsg = $('streaming');
        if (streamMsg) streamMsg.removeAttribute('id');

        if (e.name === 'AbortError') {
            status.className = 'status';
            status.textContent = 'Stopped';
        } else {
            setStatusError('Error: ' + e.message);
        }

        resetStreamingState();
        setSendButtonIdle();
        return;
    }
}

async function cancelServerRun() {
    try { abortController?.abort(); } catch {}
    try {
        if (!currentSid) return;
        await fetch('/api/chat/cancel', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({sid: currentSid})
        });
    } catch {}
}

async function loadModels() {
    try {
        const res = await fetch('/api/models');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        if (data.models) {
            availableModels = data.models;
            modelSelect.innerHTML = '';
            data.models.sort((a,b) => (a.is_free && !b.is_free) ? -1 : (!a.is_free && b.is_free) ? 1 : a.name.localeCompare(b.name));
            for (const m of data.models) {
                const o = document.createElement('option');
                o.value = m.id;
                o.textContent = m.name + (m.is_free ? ' (free)' : '');
                if (m.id === currentModel) o.selected = true;
                modelSelect.appendChild(o);
            }
            syncReasoningUI();
        } else {
            throw new Error('No models returned');
        }
    } catch (e) {
        availableModels = [];
        modelSelect.textContent = '';
        const fallback = document.createElement('option');
        fallback.value = currentModel;
        fallback.textContent = modelDisplayName(currentModel);
        modelSelect.appendChild(fallback);
        setStatusError('Unable to load models; using current model');
        syncReasoningUI();
    }
}

// Event listeners
btnSend.onclick = () => isStreaming ? cancelServerRun() : sendMessage();
inputBox.onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!isStreaming) sendMessage(); }};
inputBox.oninput = () => {
    if (inputResizeRAF) return;
    inputResizeRAF = requestAnimationFrame(() => {
        inputResizeRAF = 0;
        inputBox.style.height = '0';
        inputBox.style.height = Math.min(inputBox.scrollHeight, INPUT_MAX_HEIGHT_PX) + 'px';
    });
};
btnSettings.onclick = toggleSettingsPanel;
btnClear.onclick = () => {
    if (isStreaming) {
        // Best-effort server cancel; UI reset happens immediately below.
        cancelServerRun();
    }
    messageHistory = [...SYSTEM_PROMPTS];
    resetStreamingState();
    chatContainer.innerHTML = '';
    emptyState.style.display = 'flex';
    chatContainer.appendChild(emptyState);
    setSendButtonIdle();
    setStatusIdle();
};
modelSelect.onchange = () => { currentModel = modelSelect.value; currentModelDisplay.textContent = modelDisplayName(currentModel); syncReasoningUI(); closeSettingsPanel(); };
temperature.oninput = () => {
    if (tempRAF) return;
    tempRAF = requestAnimationFrame(() => {
        tempRAF = 0;
        tempValue.textContent = temperature.value;
    });
};
document.onclick = e => { if (!settingsPanel.contains(e.target) && e.target !== btnSettings) closeSettingsPanel(); };
document.onkeydown = e => {
    if (e.key === 'Escape') {
        if (settingsPanel.classList.contains('show')) {
            closeSettingsPanel();
            return;
        }
        if (isStreaming) cancelServerRun();
        else window.close();
    }
};

window.addEventListener('beforeunload', () => {
    try {
        const sid = streamingSid || currentSid;
        if (sid && navigator.sendBeacon) {
            navigator.sendBeacon('/api/chat/closed', JSON.stringify({sid}));
        }
    } catch {}
    try { abortController?.abort(); } catch {}
    try { if (streamTimer) clearInterval(streamTimer); } catch {}
    closeStreamSource();
    streamTimer = null;
});

btnAttach.onclick = () => fileInput.click();
fileInput.onchange = () => { addAttachmentsFromFiles(fileInput.files); fileInput.value = ''; };

inputBox.addEventListener('paste', (e) => {
    const items = e.clipboardData?.items ? Array.from(e.clipboardData.items) : [];
    const files = items.filter(it => it.kind === 'file').map(it => it.getAsFile()).filter(Boolean);
    if (files.length) {
        e.preventDefault();
        addAttachmentsFromFiles(files);
    }
});

inputBox.addEventListener('dragover', (e) => { e.preventDefault(); });
inputBox.addEventListener('drop', (e) => {
    e.preventDefault();
    if (e.dataTransfer?.files?.length) addAttachmentsFromFiles(e.dataTransfer.files);
});
