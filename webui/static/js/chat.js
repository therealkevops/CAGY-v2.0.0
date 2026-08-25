const ChatManager = {
  chatMessages: document.getElementById("chat-messages"),
  chatForm: document.getElementById("chat-form"),
  chatInput: document.getElementById("chat-input"),
  btnSend: document.getElementById("btn-send"),
  btnClearChat: document.getElementById("btn-clear-chat"),
  isStreaming: false,

  init() {
    this.chatForm.addEventListener("submit", (e) => {
      e.preventDefault();
      this.sendMessage();
    });

    this.chatInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        this.sendMessage();
      }
    });

    this.btnClearChat.addEventListener("click", () => this.clearHistory());
  },

  async clearHistory() {
    try {
      await fetch("/api/chat/clear", { method: "POST" });
      this.chatMessages.innerHTML = `
        <div class="message system-msg">
          <div class="msg-bubble">Session history cleared. Ready for a new conversation.</div>
        </div>
      `;
    } catch (e) {
      console.error(e);
    }
  },

  appendMessage(role, text) {
    const msgEl = document.createElement("div");
    msgEl.className = `message ${role}-msg`;

    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";

    if (role === "user") {
      bubble.textContent = text;
    } else {
      if (window.marked && typeof marked.parse === "function") {
        bubble.innerHTML = marked.parse(text);
      } else {
        // Plain text fallback with line breaks
        bubble.innerHTML = text
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/\n/g, "<br>");
      }
      if (window.hljs && typeof hljs.highlightElement === "function") {
        bubble.querySelectorAll("pre code").forEach(block => hljs.highlightElement(block));
      }
    }

    msgEl.appendChild(bubble);
    this.chatMessages.appendChild(msgEl);
    this.scrollToBottom();
    return bubble;
  },

  appendToolCard(toolName, args) {
    const card = document.createElement("div");
    card.className = "tool-card";

    const header = document.createElement("div");
    header.className = "tool-card-header";
    header.innerHTML = `
      <div>
        <span class="tool-badge">TOOL</span>
        <strong>${toolName}</strong>
      </div>
      <span class="status-indicator">⏳ Running...</span>
    `;

    const body = document.createElement("div");
    body.className = "tool-card-body";
    body.textContent = `Args: ${JSON.stringify(args, null, 2)}`;

    header.addEventListener("click", () => {
      body.classList.toggle("hidden");
    });

    card.appendChild(header);
    card.appendChild(body);
    this.chatMessages.appendChild(card);
    this.scrollToBottom();

    return {
      updateResult: (result) => {
        const indicator = header.querySelector(".status-indicator");
        if (indicator) {
          indicator.textContent = result.success === false ? "❌ Failed" : "✓ Completed";
          indicator.style.color = result.success === false ? "var(--danger)" : "var(--success)";
        }
        body.textContent = `Args: ${JSON.stringify(args, null, 2)}\n\nResult:\n${JSON.stringify(result, null, 2)}`;
      }
    };
  },

  async sendMessage() {
    const message = this.chatInput.value.trim();
    if (!message || this.isStreaming) return;

    this.appendMessage("user", message);
    this.chatInput.value = "";
    this.chatInput.disabled = true;
    this.btnSend.disabled = true;
    this.isStreaming = true;

    let currentAgentBubble = null;
    let accumulatedText = "";
    let activeToolCard = null;

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message })
      });

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop();

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            const jsonStr = line.replace("data: ", "").trim();
            if (!jsonStr) continue;

            try {
              const event = JSON.parse(jsonStr);

              if (event.type === "text") {
                if (!currentAgentBubble) {
                  currentAgentBubble = this.appendMessage("agent", "");
                }
                accumulatedText += event.content;
                if (window.marked && typeof marked.parse === "function") {
                  currentAgentBubble.innerHTML = marked.parse(accumulatedText);
                } else {
                  currentAgentBubble.innerHTML = accumulatedText
                    .replace(/&/g, "&amp;")
                    .replace(/</g, "&lt;")
                    .replace(/>/g, "&gt;")
                    .replace(/\n/g, "<br>");
                }
                if (window.hljs && typeof hljs.highlightElement === "function") {
                  currentAgentBubble.querySelectorAll("pre code").forEach(block => hljs.highlightElement(block));
                }
                this.scrollToBottom();
              } else if (event.type === "tool_call") {
                activeToolCard = this.appendToolCard(event.name, event.args);
              } else if (event.type === "tool_result") {
                if (activeToolCard) {
                  activeToolCard.updateResult(event.result);
                  activeToolCard = null;
                }
                // Reset current bubble so next text part starts fresh bubble after tool call
                currentAgentBubble = null;
                accumulatedText = "";
              } else if (event.type === "error") {
                const errEl = document.createElement("div");
                errEl.className = "message system-msg";
                errEl.innerHTML = `<div class="msg-bubble" style="color: var(--danger); border-color: var(--danger);">⚠️ ${event.content}</div>`;
                this.chatMessages.appendChild(errEl);
                this.scrollToBottom();
              }
            } catch (err) {
              console.error("Failed to parse SSE event:", err, jsonStr);
            }
          }
        }
      }
    } catch (e) {
      const errEl = document.createElement("div");
      errEl.className = "message system-msg";
      errEl.innerHTML = `<div class="msg-bubble" style="color: var(--danger);">Connection failed: ${e.message}</div>`;
      this.chatMessages.appendChild(errEl);
    } finally {
      this.chatInput.disabled = false;
      this.btnSend.disabled = false;
      this.chatInput.focus();
      this.isStreaming = false;
    }
  },

  scrollToBottom() {
    this.chatMessages.scrollTop = this.chatMessages.scrollHeight;
  }
};
