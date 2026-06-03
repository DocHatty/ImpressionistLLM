            // State
            let snippets = [];
            let selectedIndex = -1;
            let storageWarningShown = false;
            const safeDecodeURIComponent = window.PromptUtils.safeDecodeURIComponent;

            function showToast(message, type = "success") {
                window.PromptUtils.showNotification({
                    target: "toast",
                    message,
                    type,
                    baseClass: "toast",
                    duration: 2500,
                });
            }

            function warnStorageUnavailable() {
                if (storageWarningShown) return;
                storageWarningShown = true;
                showToast("Browser storage is unavailable. Changes may not persist.", "warning");
            }

            function safeStorageGet(key) {
                try {
                    return localStorage.getItem(key);
                } catch (e) {
                    warnStorageUnavailable();
                    return null;
                }
            }

            function safeStorageSet(key, value) {
                try {
                    localStorage.setItem(key, value);
                    return true;
                } catch (e) {
                    warnStorageUnavailable();
                    return false;
                }
            }

            function normalizeSnippets(value) {
                if (!Array.isArray(value)) return [];
                return value
                    .filter((item) => item && typeof item === "object")
                    .map((item, index) => ({
                        name: String(item.name || `Snippet ${index + 1}`),
                        content: String(item.content || ""),
                    }));
            }

            // Load snippets from URL params (fresh capture) or localStorage (persisted)
            function init() {
                const params = new URLSearchParams(window.location.search);
                const urlContent = params.get("content");
                const urlCount = parseInt(params.get("count")) || 1;

                // URL params take priority (fresh capture from AHK)
                if (urlContent) {
                    const decodedContent = safeDecodeURIComponent(urlContent);

                    // Parse multi-segment content if count > 1
                    if (urlCount > 1 && decodedContent.includes("--- ")) {
                        const parts = decodedContent.split(/\n\n--- /);

                        if (parts[0].trim()) {
                            snippets.push({
                                name: "Context 1",
                                content: parts[0].trim(),
                            });
                        }

                        for (let i = 1; i < parts.length; i++) {
                            const part = parts[i];
                            const markerEnd = part.indexOf(" ---\n");
                            if (markerEnd > -1) {
                                const name = part.substring(0, markerEnd);
                                const content = part
                                    .substring(markerEnd + 5)
                                    .trim();
                                if (content) {
                                    snippets.push({
                                        name: name,
                                        content: content,
                                    });
                                }
                            } else {
                                snippets.push({
                                    name: `Context ${i + 1}`,
                                    content: part.trim(),
                                });
                            }
                        }
                    } else {
                        snippets.push({
                            name: "Context 1",
                            content: decodedContent,
                        });
                    }

                    save();
                    window.history.replaceState(
                        {},
                        "",
                        window.location.pathname,
                    );
                } else {
                    const saved = safeStorageGet("contextSnippets");
                    if (saved) {
                        try {
                            snippets = normalizeSnippets(JSON.parse(saved));
                        } catch (e) {
                            snippets = [];
                        }
                    }
                }

                snippets = normalizeSnippets(snippets);
                renderList();

                if (snippets.length > 0) {
                    selectSnippet(0);
                }
            }

            function save() {
                safeStorageSet("contextSnippets", JSON.stringify(snippets));
            }

            function renderList() {
                const list = document.getElementById("snippetList");
                list.replaceChildren();

                if (snippets.length === 0) {
                    const empty = document.createElement("div");
                    empty.className = "list-empty";
                    empty.textContent = "No snippets yet";
                    list.append(empty);
                    return;
                }

                const fragment = document.createDocumentFragment();
                snippets.forEach((snippet, index) => {
                    const item = document.createElement("div");
                    item.className = `list-item ${index === selectedIndex ? "active" : ""}`;
                    item.setAttribute("role", "button");
                    item.tabIndex = 0;
                    item.addEventListener("click", () => selectSnippet(index));
                    item.addEventListener("keydown", (event) => {
                        if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            selectSnippet(index);
                        }
                    });

                    const info = document.createElement("div");
                    info.className = "list-item-content";

                    const title = document.createElement("div");
                    title.className = "list-item-title";
                    title.textContent = snippet.name || "Untitled";

                    const meta = document.createElement("div");
                    meta.className = "list-item-meta";
                    meta.textContent = `${snippet.content.length} chars`;

                    info.append(title, meta);
                    item.append(info);
                    fragment.append(item);
                });
                list.append(fragment);
            }

            function selectSnippet(index) {
                if (selectedIndex >= 0 && selectedIndex < snippets.length) {
                    snippets[selectedIndex].name =
                        document.getElementById("snippetName").value;
                    snippets[selectedIndex].content =
                        document.getElementById("snippetContent").value;
                }

                selectedIndex = index;

                if (index >= 0 && index < snippets.length) {
                    document.getElementById("emptyState").style.display =
                        "none";
                    document
                        .getElementById("editorContent")
                        .classList.add("visible");

                    document.getElementById("snippetName").value =
                        snippets[index].name;
                    document.getElementById("snippetContent").value =
                        snippets[index].content;
                    updateCharCount();
                } else {
                    document.getElementById("emptyState").style.display =
                        "flex";
                    document
                        .getElementById("editorContent")
                        .classList.remove("visible");
                }

                renderList();
                save();
            }

            function newSnippet() {
                snippets.push({
                    name: `Snippet ${snippets.length + 1}`,
                    content: "",
                });
                selectSnippet(snippets.length - 1);
                document.getElementById("snippetName").focus();
                document.getElementById("snippetName").select();
                showToast("New snippet created", "success");
            }

            function deleteSnippet() {
                if (selectedIndex < 0) return;

                snippets.splice(selectedIndex, 1);

                if (snippets.length === 0) {
                    selectedIndex = -1;
                } else if (selectedIndex >= snippets.length) {
                    selectedIndex = snippets.length - 1;
                }

                if (selectedIndex >= 0) {
                    selectSnippet(selectedIndex);
                } else {
                    document.getElementById("emptyState").style.display =
                        "flex";
                    document
                        .getElementById("editorContent")
                        .classList.remove("visible");
                    renderList();
                }

                save();
                showToast("Snippet deleted", "success");
            }

            function clearAll() {
                if (snippets.length === 0) return;

                if (confirm("Clear all snippets?")) {
                    snippets = [];
                    selectedIndex = -1;
                    document.getElementById("emptyState").style.display =
                        "flex";
                    document
                        .getElementById("editorContent")
                        .classList.remove("visible");
                    renderList();
                    save();
                    showToast("All snippets cleared", "success");
                }
            }

            function applyAndClose() {
                if (selectedIndex >= 0 && selectedIndex < snippets.length) {
                    snippets[selectedIndex].name =
                        document.getElementById("snippetName").value;
                    snippets[selectedIndex].content =
                        document.getElementById("snippetContent").value;
                }
                save();

                let combined = "";
                snippets.forEach((s, i) => {
                    if (!s.content.trim()) return;
                    if (combined) combined += "\n\n--- " + s.name + " ---\n";
                    combined += s.content;
                });

                if (window.opener) {
                    window.opener.postMessage(
                        {
                            type: "context-update",
                            content: combined,
                            count: snippets.length,
                        },
                        "*",
                    );
                    window.close();
                } else {
                    safeStorageSet("contextContent", combined);
                    safeStorageSet("contextCount", snippets.length.toString());
                    showToast(
                        `Applied ${snippets.length} snippets (${combined.length} chars)`,
                        "success",
                    );
                }
            }

            function updateCharCount() {
                const content = document.getElementById("snippetContent").value;
                document.getElementById("charCount").textContent =
                    `${content.length} characters`;
            }

            // Event listeners
            document
                .getElementById("snippetName")
                .addEventListener("input", function () {
                    if (selectedIndex >= 0) {
                        snippets[selectedIndex].name = this.value;
                        renderList();
                    }
                });

            document
                .getElementById("snippetContent")
                .addEventListener("input", function () {
                    if (selectedIndex >= 0) {
                        snippets[selectedIndex].content = this.value;
                    }
                    updateCharCount();
                });

            document
                .getElementById("snippetContent")
                .addEventListener("blur", save);
            document
                .getElementById("snippetName")
                .addEventListener("blur", save);

            document
                .getElementById("clearAllBtn")
                .addEventListener("click", clearAll);
            document
                .getElementById("applyCloseBtn")
                .addEventListener("click", applyAndClose);
            document
                .getElementById("newSnippetBtn")
                .addEventListener("click", newSnippet);
            document
                .getElementById("deleteSnippetBtn")
                .addEventListener("click", deleteSnippet);

            // Keyboard shortcuts
            document.addEventListener("keydown", function (e) {
                if (e.ctrlKey || e.metaKey) {
                    if (e.key === "n") {
                        e.preventDefault();
                        newSnippet();
                    } else if (e.key === "s") {
                        e.preventDefault();
                        save();
                        showToast("Saved", "success");
                    } else if (e.key === "Enter") {
                        e.preventDefault();
                        applyAndClose();
                    }
                }
            });

            // Initialize
            init();
        
