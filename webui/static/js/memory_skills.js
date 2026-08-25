const MemorySkillsManager = {
  memoryEditor: document.getElementById("memory-editor"),
  btnSaveMemory: document.getElementById("btn-save-memory"),
  memoryStatus: document.getElementById("memory-status"),
  skillsList: document.getElementById("skills-list"),
  btnRefreshSkills: document.getElementById("btn-refresh-skills"),
  skillDetailModal: document.getElementById("skill-detail-modal"),
  skillDetailTitle: document.getElementById("skill-detail-title"),
  skillDetailBody: document.getElementById("skill-detail-body"),
  btnCloseSkillDetail: document.getElementById("btn-close-skill-detail"),
  btnUseSkill: document.getElementById("btn-use-skill"),
  activeSkill: null,

  init() {
    // Tabs
    document.querySelectorAll(".tab-btn").forEach(btn => {
      btn.addEventListener("click", (e) => {
        document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
        document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
        
        btn.classList.add("active");
        const targetTab = document.getElementById(btn.dataset.tab);
        if (targetTab) targetTab.classList.add("active");
      });
    });

    // Memory events
    this.btnSaveMemory.addEventListener("click", () => this.saveMemory());
    this.loadMemory();

    // Skills events
    this.btnRefreshSkills.addEventListener("click", () => this.loadSkills());
    this.btnCloseSkillDetail.addEventListener("click", () => this.skillDetailModal.classList.add("hidden"));
    this.btnUseSkill.addEventListener("click", () => this.insertSkillToChat());
    this.loadSkills();
  },

  async loadMemory() {
    try {
      const res = await fetch("/api/memory");
      const data = await res.json();
      this.memoryEditor.value = data.content || "";
    } catch (e) {
      this.memoryStatus.textContent = `Failed to load memory: ${e.message}`;
      this.memoryStatus.style.color = "var(--danger)";
    }
  },

  async saveMemory() {
    this.memoryStatus.textContent = "Saving memory...";
    this.memoryStatus.style.color = "var(--text-muted)";
    try {
      const res = await fetch("/api/memory", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: this.memoryEditor.value })
      });
      const data = await res.json();
      if (data.success) {
        this.memoryStatus.textContent = "✓ Memory saved and active.";
        this.memoryStatus.style.color = "var(--success)";
      } else {
        this.memoryStatus.textContent = `Error: ${data.error}`;
        this.memoryStatus.style.color = "var(--danger)";
      }
    } catch (e) {
      this.memoryStatus.textContent = `Save failed: ${e.message}`;
      this.memoryStatus.style.color = "var(--danger)";
    }
  },

  async loadSkills() {
    this.skillsList.innerHTML = "<div style='color: var(--text-muted); font-size: 11px;'>Discovering skills...</div>";
    try {
      const res = await fetch("/api/skills");
      const data = await res.json();
      this.skillsList.innerHTML = "";

      if (!data.skills || data.skills.length === 0) {
        this.skillsList.innerHTML = "<div style='color: var(--text-muted); font-size: 11px;'>(No SKILL.md found in workspace)</div>";
        return;
      }

      data.skills.forEach(skill => {
        const card = document.createElement("div");
        card.className = "skill-card";
        card.innerHTML = `
          <div class="skill-title">⚡ ${skill.name}</div>
          <div class="skill-desc">${skill.description}</div>
        `;
        card.addEventListener("click", () => this.showSkillDetail(skill));
        this.skillsList.appendChild(card);
      });
    } catch (e) {
      this.skillsList.innerHTML = `<div style='color: var(--danger);'>Error: ${e.message}</div>`;
    }
  },

  showSkillDetail(skill) {
    this.activeSkill = skill;
    this.skillDetailTitle.textContent = skill.name;
    this.skillDetailBody.textContent = skill.body || skill.content;
    this.skillDetailModal.classList.remove("hidden");
  },

  insertSkillToChat() {
    if (!this.activeSkill) return;
    const input = document.getElementById("chat-input");
    input.value = `Please execute skill: ${this.activeSkill.name}\n\nInstructions:\n${this.activeSkill.body}\n`;
    input.focus();
  }
};
