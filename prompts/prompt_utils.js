(function (global) {
    "use strict";

    const notificationTimers = new WeakMap();

    function escapeHtml(value) {
        return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;",
        })[ch]);
    }

    function jsArg(value) {
        return escapeHtml(JSON.stringify(String(value ?? "")));
    }

    function safeDecodeURIComponent(value) {
        if (!value) return value;
        try {
            return decodeURIComponent(value);
        } catch {
            const fixed = String(value).replace(/%(?![0-9A-Fa-f]{2})/g, "%25");
            try {
                return decodeURIComponent(fixed);
            } catch (fixedError) {
                console.warn("Could not decode URI component:", fixedError);
                return value;
            }
        }
    }

    function modelDisplayName(model) {
        const modelId = String(model || "");
        return modelId.split("/").pop() || modelId;
    }

    function modelSupportsParameter(model, key) {
        const supported = model?.supported_parameters || model?.supportedParameters || [];
        return Array.isArray(supported) && supported.includes(key);
    }

    function showNotification({
        target,
        message,
        type = "success",
        baseClass = "toast",
        showClass = "show",
        duration = 2500,
    }) {
        const element = typeof target === "string" ? document.getElementById(target) : target;
        if (!element) return;

        const previousTimer = notificationTimers.get(element);
        if (previousTimer) {
            clearTimeout(previousTimer);
            notificationTimers.delete(element);
        }

        element.textContent = message;
        element.className = `${baseClass} ${type} ${showClass}`;

        if (duration > 0) {
            const timer = setTimeout(() => {
                element.classList.remove(showClass);
                notificationTimers.delete(element);
            }, duration);
            notificationTimers.set(element, timer);
        }
    }

    global.PromptUtils = Object.freeze({
        escapeHtml,
        jsArg,
        safeDecodeURIComponent,
        modelDisplayName,
        modelSupportsParameter,
        showNotification,
    });
})(window);
