document.addEventListener("DOMContentLoaded", () => {
  // Initialize subsystems
  FileExplorer.init();
  MemorySkillsManager.init();
  ChatManager.init();

  // Theme toggle
  const btnToggleTheme = document.getElementById("btn-toggle-theme");
  btnToggleTheme.addEventListener("click", () => {
    document.body.classList.toggle("light-theme");
    document.body.classList.toggle("dark-theme");
  });

  // Fetch status info
  fetch("/api/status")
    .then(res => res.json())
    .then(data => {
      const modelBadge = document.getElementById("model-badge");
      const workspaceBadge = document.getElementById("workspace-badge");
      if (modelBadge && data.model) modelBadge.textContent = data.model;
      if (workspaceBadge && data.workspace) workspaceBadge.textContent = `Workspace: ${data.workspace}`;
    })
    .catch(console.error);
});
