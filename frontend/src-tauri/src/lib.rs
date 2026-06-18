use std::sync::Arc;
use std::time::Duration;
use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::TrayIconBuilder;
use tauri::Manager;
use tauri_plugin_autostart::MacosLauncher;
use tokio::sync::Mutex;

const OLLAMA_PORT: u16 = 11434;
const JARVIS_PORT: u16 = 7710;
const LITELLM_PORT: u16 = 4000;
const QWEN_FAST_LANE_PORT: u16 = 8084;

/// Studio's local-first model route. LiteLLM handles this alias and falls back
/// while the WSL Qwen runtime is still warming.
const STARTUP_MODEL: &str = "qwen3.6-27b-local";
const STARTUP_OLLAMA_MODEL: &str = "qwen3.6:27b";

/// Get the user home directory, handling both Unix (HOME) and Windows (USERPROFILE).
fn home_dir() -> String {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_default()
}

/// Resolve full path to a binary by checking common locations.
/// macOS .app bundles don't inherit the shell PATH, so we probe manually.
fn resolve_bin(name: &str) -> String {
    let home = home_dir();

    #[cfg(not(target_os = "windows"))]
    let candidates = vec![
        format!("/opt/homebrew/bin/{name}"),
        format!("{home}/.local/bin/{name}"),
        format!("{home}/.cargo/bin/{name}"),
        format!("/usr/local/bin/{name}"),
        format!("/usr/bin/{name}"),
    ];

    #[cfg(target_os = "windows")]
    let candidates = {
        let localappdata = std::env::var("LOCALAPPDATA").unwrap_or_default();
        let programfiles = std::env::var("ProgramFiles").unwrap_or_default();
        let programfiles_x86 = std::env::var("ProgramFiles(x86)").unwrap_or_default();
        vec![
            // Git for Windows — standard install paths
            format!("{programfiles}\\Git\\cmd\\{name}.exe"),
            format!("{programfiles_x86}\\Git\\cmd\\{name}.exe"),
            format!("{localappdata}\\Programs\\Git\\cmd\\{name}.exe"),
            // Scoop package manager
            format!("{home}\\scoop\\shims\\{name}.exe"),
            // Cargo, local bin
            format!("{home}\\.cargo\\bin\\{name}.exe"),
            format!("{home}\\.local\\bin\\{name}.exe"),
            // Generic program locations
            format!("{localappdata}\\Programs\\{name}\\{name}.exe"),
            format!("{programfiles}\\{name}\\{name}.exe"),
            // Ollama installs to LOCALAPPDATA on Windows
            format!("{localappdata}\\Programs\\Ollama\\{name}.exe"),
            // uv installs via pip/pipx
            format!("{home}\\AppData\\Roaming\\Python\\Scripts\\{name}.exe"),
        ]
    };

    for path in &candidates {
        if std::path::Path::new(path).exists() {
            return path.clone();
        }
    }

    // Fallback: ask the OS to find it on PATH.
    // On Windows this uses `where.exe`, on Unix `which`.
    #[cfg(target_os = "windows")]
    {
        if let Ok(output) = std::process::Command::new("where")
            .arg(format!("{name}.exe"))
            .output()
        {
            if output.status.success() {
                let stdout = String::from_utf8_lossy(&output.stdout);
                if let Some(first_line) = stdout.lines().next() {
                    let p = first_line.trim();
                    if !p.is_empty() && std::path::Path::new(p).exists() {
                        return p.to_string();
                    }
                }
            }
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        if let Ok(output) = std::process::Command::new("which").arg(name).output() {
            if output.status.success() {
                let stdout = String::from_utf8_lossy(&output.stdout);
                if let Some(first_line) = stdout.lines().next() {
                    let p = first_line.trim();
                    if !p.is_empty() && std::path::Path::new(p).exists() {
                        return p.to_string();
                    }
                }
            }
        }
    }

    name.to_string()
}

/// Find the OpenJarvis project root (contains pyproject.toml).
/// Checks OPENJARVIS_ROOT env var, walks up from the executable, then
/// probes common clone locations.
fn find_project_root() -> Option<std::path::PathBuf> {
    // 1. Explicit env var override
    if let Ok(root) = std::env::var("OPENJARVIS_ROOT") {
        let path = std::path::PathBuf::from(&root);
        if path.join("pyproject.toml").exists() {
            return Some(path);
        }
    }

    // 2. Walk up from the running executable (works in dev and .app bundle)
    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.parent().map(|p| p.to_path_buf());
        for _ in 0..8 {
            if let Some(ref d) = dir {
                if d.join("pyproject.toml").exists() {
                    return Some(d.clone());
                }
                dir = d.parent().map(|p| p.to_path_buf());
            }
        }
    }

    // 3. Fallback: well-known direct paths
    let home = home_dir();
    let direct = [
        format!("{home}/OpenJarvis"),
        format!("{home}/projects/hazy/OpenJarvis"),
        format!("{home}/projects/OpenJarvis"),
        format!("{home}/src/OpenJarvis"),
        format!("{home}/Documents/OpenJarvis"),
        format!("{home}/Desktop/OpenJarvis"),
        format!("{home}/Developer/OpenJarvis"),
        format!("{home}/dev/OpenJarvis"),
        format!("{home}/Code/OpenJarvis"),
        format!("{home}/code/OpenJarvis"),
        format!("{home}/repos/OpenJarvis"),
        format!("{home}/github/OpenJarvis"),
    ];
    for p in &direct {
        let path = std::path::PathBuf::from(p);
        if path.join("pyproject.toml").exists() {
            return Some(path);
        }
    }

    // 4. Shallow scan: look for OpenJarvis one level inside common parent dirs.
    //    This catches clones like ~/Documents/my-stuff/OpenJarvis without
    //    needing to enumerate every possible intermediate folder.
    let scan_parents = [
        format!("{home}/Documents"),
        format!("{home}/Desktop"),
        format!("{home}/Developer"),
        format!("{home}/projects"),
        format!("{home}/repos"),
        format!("{home}/src"),
        format!("{home}/Code"),
        format!("{home}/code"),
        format!("{home}/dev"),
        format!("{home}/github"),
    ];
    for parent in &scan_parents {
        let parent_path = std::path::PathBuf::from(parent);
        if let Ok(entries) = std::fs::read_dir(&parent_path) {
            for entry in entries.flatten() {
                let candidate = entry.path().join("OpenJarvis");
                if candidate.join("pyproject.toml").exists() {
                    return Some(candidate);
                }
                // Also check if the entry itself is OpenJarvis (case-insensitive match)
                if let Some(name) = entry.file_name().to_str() {
                    if name.eq_ignore_ascii_case("openjarvis")
                        && entry.path().join("pyproject.toml").exists()
                    {
                        return Some(entry.path());
                    }
                }
            }
        }
    }

    None
}

// ---------------------------------------------------------------------------
// BackendManager — owns the Ollama + Jarvis server child processes
// ---------------------------------------------------------------------------

struct ChildHandle {
    child: tokio::process::Child,
}

impl ChildHandle {
    async fn kill(&mut self) {
        let _ = self.child.kill().await;
        let _ = self.child.wait().await;
    }
}

#[derive(Default)]
struct BackendManager {
    ollama: Option<ChildHandle>,
    jarvis: Option<ChildHandle>,
    litellm: Option<ChildHandle>,
    qwen_fast_lane: Option<ChildHandle>,
    // True when this launch ADOPTED an already-running stack instead of
    // spawning it (the reuse fast-path). We must not tear down a stack we
    // didn't start — closing the app should leave the operator's manual
    // `start-studio-stack.ps1` (or a prior app instance) running.
    adopted: bool,
}

impl BackendManager {
    async fn stop_all_runtime_processes(&mut self) {
        if let Some(ref mut h) = self.jarvis {
            h.kill().await;
        }
        self.jarvis = None;
        if let Some(ref mut h) = self.litellm {
            h.kill().await;
        }
        self.litellm = None;
        if let Some(ref mut h) = self.qwen_fast_lane {
            h.kill().await;
        }
        self.qwen_fast_lane = None;
        if let Some(ref mut h) = self.ollama {
            h.kill().await;
        }
        self.ollama = None;
        // Only port-kill stacks we own. An adopted stack stays up so the next
        // launch reuses it (no kill→uv-sync→relaunch churn) and so a manual
        // operator stack survives the app closing.
        if !self.adopted {
            clear_stale_runtime_ports().await;
        }
    }
}

type SharedBackend = Arc<Mutex<BackendManager>>;

fn stop_all_blocking(backend: SharedBackend) {
    tauri::async_runtime::block_on(async move {
        backend.lock().await.stop_all_runtime_processes().await;
    });
}

// ---------------------------------------------------------------------------
// Setup status (reported to frontend)
// ---------------------------------------------------------------------------

#[derive(serde::Serialize, Clone)]
struct SetupStatus {
    phase: String,
    detail: String,
    ollama_ready: bool,
    server_ready: bool,
    model_ready: bool,
    error: Option<String>,
}

impl Default for SetupStatus {
    fn default() -> Self {
        Self {
            phase: "starting".into(),
            detail: "Initializing...".into(),
            ollama_ready: false,
            server_ready: false,
            model_ready: false,
            error: None,
        }
    }
}

type SharedStatus = Arc<Mutex<SetupStatus>>;

// ---------------------------------------------------------------------------
// Health-check helpers
// ---------------------------------------------------------------------------

async fn wait_for_url(url: &str, timeout: Duration) -> bool {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();
    let deadline = tokio::time::Instant::now() + timeout;
    while tokio::time::Instant::now() < deadline {
        if let Ok(resp) = client.get(url).send().await {
            if resp.status().is_success() {
                return true;
            }
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    false
}

#[cfg(target_os = "windows")]
fn hide_command_window(cmd: &mut tokio::process::Command) {
    const CREATE_NO_WINDOW: u32 = 0x08000000;
    cmd.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(target_os = "windows"))]
fn hide_command_window(_cmd: &mut tokio::process::Command) {}

// The runtime watchdog should fire ONLY while the desktop app is open. The
// watchdog also gates itself (skipped_no_app), but the operator wants the
// scheduled task to not even wake when the app is closed (gaming). So we
// enable its task on launch and disable it on exit. Best-effort: a missing
// task or permission error is non-fatal — the in-process gate is the backstop.
#[cfg(target_os = "windows")]
fn set_watchdog_task_enabled(enabled: bool) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x08000000;
    let flag = if enabled { "/ENABLE" } else { "/DISABLE" };
    let _ = std::process::Command::new("schtasks")
        .args(["/Change", "/TN", "JarvisRuntimeWatchdog", flag])
        .creation_flags(CREATE_NO_WINDOW)
        .output();
}

#[cfg(not(target_os = "windows"))]
fn set_watchdog_task_enabled(_enabled: bool) {}

#[cfg(target_os = "windows")]
async fn kill_listening_processes_on_ports(ports: &[u16]) {
    if ports.is_empty() {
        return;
    }
    let port_list = ports
        .iter()
        .map(|port| port.to_string())
        .collect::<Vec<_>>()
        .join(",");
    let command = format!(
        "$ports=@({}); \
         Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | \
         Where-Object {{ $ports -contains $_.LocalPort }} | \
         Select-Object -ExpandProperty OwningProcess -Unique | \
         Where-Object {{ $_ -gt 0 -and $_ -ne $PID }} | \
         ForEach-Object {{ Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }}",
        port_list
    );
    let mut cmd = tokio::process::Command::new("powershell.exe");
    cmd.args([
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        &command,
    ])
    .stdout(std::process::Stdio::null())
    .stderr(std::process::Stdio::null());
    hide_command_window(&mut cmd);
    let _ = cmd.status().await;

    let wsl_cleanup = "$matches = Get-CimInstance Win32_Process | \
        Where-Object { $_.Name -eq 'wsl.exe' -and $_.CommandLine -match 'llama-server' -and $_.CommandLine -match '--port 8084' }; \
        foreach ($match in $matches) { Stop-Process -Id $match.ProcessId -Force -ErrorAction SilentlyContinue }";
    let mut wsl_cmd = tokio::process::Command::new("powershell.exe");
    wsl_cmd
        .args([
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            wsl_cleanup,
        ])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    hide_command_window(&mut wsl_cmd);
    let _ = wsl_cmd.status().await;

    let mut wsl_port_cmd = tokio::process::Command::new("wsl.exe");
    wsl_port_cmd
        .args([
            "-d",
            "JarvisUbuntu",
            "--",
            "bash",
            "-lc",
            "fuser -k 8084/tcp >/dev/null 2>&1 || true",
        ])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    hide_command_window(&mut wsl_port_cmd);
    let _ = wsl_port_cmd.status().await;
}

#[cfg(not(target_os = "windows"))]
async fn kill_listening_processes_on_ports(ports: &[u16]) {
    for port in ports {
        let _ = tokio::process::Command::new("fuser")
            .args(["-k", &format!("{}/tcp", port)])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .await;
    }
}

async fn clear_stale_runtime_ports() {
    kill_listening_processes_on_ports(&[JARVIS_PORT, LITELLM_PORT, QWEN_FAST_LANE_PORT]).await;
    tokio::time::sleep(Duration::from_millis(900)).await;
}

fn configure_uv_command(cmd: &mut tokio::process::Command) {
    let cache_dir = std::env::temp_dir().join("uv-cache-openjarvis");
    cmd.env("UV_CACHE_DIR", cache_dir);
    cmd.env("UV_LINK_MODE", "copy");
}

async fn qwen_fast_lane_ready() -> bool {
    let url = format!("http://127.0.0.1:{}/health", QWEN_FAST_LANE_PORT);
    wait_for_url(&url, Duration::from_secs(1)).await
}

async fn litellm_ready() -> bool {
    let url = format!("http://127.0.0.1:{}/v1/models", LITELLM_PORT);
    wait_for_url(&url, Duration::from_secs(1)).await
}

async fn start_qwen_fast_lane(
    root: &std::path::Path,
    backend: SharedBackend,
    status: SharedStatus,
) -> bool {
    if qwen_fast_lane_ready().await {
        return true;
    }

    // Use the 30B-Coder lane (plain MoE, 64K ctx): the 27B-MTP froggeric lane
    // segfaults in its speculative sampler under agentic load. Coder is stable.
    let script = root
        .join("scripts")
        .join("start-qwen3-coder-30b-a3b-wsl.ps1");
    if !script.exists() {
        let mut s = status.lock().await;
        s.detail = format!("Qwen lane script missing: {}", script.display());
        return false;
    }

    {
        let mut s = status.lock().await;
        s.phase = "qwen-lane".into();
        s.detail = "Starting Qwen3-Coder 30B lane (stable, 64K)...".into();
    }

    let mut cmd = tokio::process::Command::new("powershell.exe");
    cmd.args([
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        &script.display().to_string(),
        "-Port",
        &QWEN_FAST_LANE_PORT.to_string(),
        "-ContextTokens",
        "65536",
    ])
    .stdout(std::process::Stdio::null())
    .stderr(std::process::Stdio::null())
    .current_dir(root);
    hide_command_window(&mut cmd);

    match cmd.spawn() {
        Ok(child) => {
            backend.lock().await.qwen_fast_lane = Some(ChildHandle { child });
        }
        Err(_) => {
            let mut s = status.lock().await;
            s.detail = "Could not launch Qwen fast lane startup script.".into();
            return false;
        }
    }

    let url = format!("http://127.0.0.1:{}/health", QWEN_FAST_LANE_PORT);
    wait_for_url(&url, Duration::from_secs(300)).await
}

async fn start_litellm_proxy(
    root: &std::path::Path,
    uv_bin: &str,
    backend: SharedBackend,
    status: SharedStatus,
) -> bool {
    if litellm_ready().await {
        return true;
    }

    {
        let mut s = status.lock().await;
        s.phase = "litellm".into();
        s.detail = "Starting LiteLLM proxy...".into();
    }

    let mut cmd = tokio::process::Command::new(uv_bin);
    configure_uv_command(&mut cmd);
    cmd.args([
        "run",
        "--no-sync",
        "litellm",
        "--config",
        "configs/litellm.yaml",
        "--port",
        &LITELLM_PORT.to_string(),
    ])
    .stdout(std::process::Stdio::null())
    .stderr(std::process::Stdio::null())
    .current_dir(root);
    hide_command_window(&mut cmd);
    cmd.env("PYTHONUTF8", "1");
    cmd.env("PYTHONIOENCODING", "utf-8");

    for (key, value) in read_cloud_keys() {
        cmd.env(&key, &value);
    }

    match cmd.spawn() {
        Ok(child) => {
            backend.lock().await.litellm = Some(ChildHandle { child });
        }
        Err(_) => {
            let mut s = status.lock().await;
            s.detail = "Could not launch LiteLLM proxy.".into();
            return false;
        }
    }

    true
}

async fn ollama_has_model(model: &str) -> bool {
    let url = format!("http://127.0.0.1:{}/api/tags", OLLAMA_PORT);
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
        .unwrap();
    if let Ok(resp) = client.get(&url).send().await {
        if let Ok(body) = resp.json::<serde_json::Value>().await {
            if let Some(models) = body.get("models").and_then(|m| m.as_array()) {
                return models.iter().any(|m| {
                    m.get("name")
                        .and_then(|n| n.as_str())
                        .map(|n| {
                            n == model
                                || n.strip_suffix(":latest") == Some(model)
                                || model.strip_suffix(":latest") == Some(n)
                        })
                        .unwrap_or(false)
                });
            }
        }
    }
    false
}

async fn pull_model(model: &str) -> Result<(), String> {
    let url = format!("http://127.0.0.1:{}/api/pull", OLLAMA_PORT);
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(600))
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client
        .post(&url)
        .json(&serde_json::json!({"name": model, "stream": false}))
        .send()
        .await
        .map_err(|e| format!("Pull request failed: {}", e))?;
    if !resp.status().is_success() {
        return Err(format!("Pull returned status {}", resp.status()));
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Backend boot sequence (runs in background after app launch)
// ---------------------------------------------------------------------------

async fn boot_backend(backend: SharedBackend, status: SharedStatus) {
    // Reuse fast-path: if a healthy Jarvis backend is already listening, ADOPT
    // it instead of tearing it down and rebuilding. The old behaviour killed
    // the server on our port every launch, then re-ran `uv sync` (~70s) and
    // relaunched — slow cold starts and process churn even when nothing was
    // wrong, and it fought the operator's manual stack / the watchdog. We now
    // only rebuild when the stack is genuinely absent or unhealthy.
    {
        let mut s = status.lock().await;
        s.phase = "probe".into();
        s.detail = "Checking for a running Jarvis stack...".into();
    }
    // /health calls engine.health(), which does a LIVE upstream probe and can
    // take several seconds — a 2s window often missed a perfectly healthy stack
    // and fell through to a needless rebuild (cold start + duplicate backends).
    // Give it a 12s window so we reliably adopt a running stack.
    if wait_for_url(
        &format!("http://127.0.0.1:{}/health", JARVIS_PORT),
        Duration::from_secs(12),
    )
    .await
    {
        backend.lock().await.adopted = true;
        let mut s = status.lock().await;
        s.ollama_ready = true;
        s.model_ready = true;
        s.server_ready = true;
        s.phase = "ready".into();
        s.detail = "Reused the running Jarvis stack.".into();
        return;
    }

    {
        let mut s = status.lock().await;
        s.phase = "cleanup".into();
        s.detail = "Clearing stale runtime ports...".into();
    }
    clear_stale_runtime_ports().await;

    // Ollama is intentionally NOT started. Jarvis routes 100% through the
    // LiteLLM proxy (:4000) → local Qwen lane (:8084). Ollama was only ever a
    // slow last-resort fallback (~1 word/20s), and starting it caused real harm:
    // a failed `ollama serve` used to abort the WHOLE boot, and a running ollama
    // got probed on every /studio/state poll and mislabelled in the UI. Removing
    // it makes startup faster and the lane routing unambiguous.
    {
        let mut s = status.lock().await;
        s.ollama_ready = true; // not used; keep the UI's "ready" gate satisfied
        s.model_ready = true;
        s.detail = "Using LiteLLM → Qwen lane (ollama disabled).".into();
    }

    // Phase 3: Start jarvis serve
    {
        let mut s = status.lock().await;
        s.phase = "server".into();
        s.detail = "Starting API server...".into();
    }

    let uv_bin = resolve_bin("uv");

    // Verify uv is actually installed
    if !std::path::Path::new(&uv_bin).exists() && uv_bin == "uv" {
        let mut s = status.lock().await;
        s.error = Some(
            "Could not find 'uv' (Python package manager). \
             Install it from https://astral.sh/uv then relaunch."
                .into(),
        );
        return;
    }

    let mut project_root = find_project_root();

    if project_root.is_none() {
        // Auto-clone on first launch
        let git_bin = resolve_bin("git");

        // Check that git is installed
        if !std::path::Path::new(&git_bin).exists() && git_bin == "git" {
            let mut s = status.lock().await;
            s.error = Some(
                "Could not find 'git'. \
                 Install it from https://git-scm.com then relaunch."
                    .into(),
            );
            return;
        }

        let target_path = std::path::PathBuf::from(home_dir()).join("OpenJarvis");
        let clone_target = target_path.display().to_string();

        // If the directory exists but is not a valid project, don't overwrite
        if target_path.exists() && !target_path.join("pyproject.toml").exists() {
            let mut s = status.lock().await;
            s.error = Some(format!(
                "{} exists but is not a valid OpenJarvis project. \
                 Remove it and relaunch, or set OPENJARVIS_ROOT to the correct path.",
                clone_target,
            ));
            return;
        }

        {
            let mut s = status.lock().await;
            s.detail = "Downloading OpenJarvis (first launch)...".into();
        }

        let clone_result = tokio::process::Command::new(&git_bin)
            .args([
                "clone",
                "--depth",
                "1",
                "https://github.com/open-jarvis/OpenJarvis.git",
                &clone_target,
            ])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::piped())
            .spawn();

        match clone_result {
            Ok(child) => match child.wait_with_output().await {
                Ok(output) if output.status.success() => {
                    project_root = Some(target_path);
                }
                Ok(output) => {
                    let stderr = String::from_utf8_lossy(&output.stderr);
                    let mut s = status.lock().await;
                    s.error = Some(format!(
                        "Failed to download OpenJarvis: {}. \
                         Clone manually: git clone https://github.com/open-jarvis/OpenJarvis.git {}",
                        stderr.trim(),
                        clone_target,
                    ));
                    return;
                }
                Err(e) => {
                    let mut s = status.lock().await;
                    s.error = Some(format!(
                        "Failed to download OpenJarvis: {}. \
                         Clone manually: git clone https://github.com/open-jarvis/OpenJarvis.git {}",
                        e, clone_target,
                    ));
                    return;
                }
            },
            Err(e) => {
                let mut s = status.lock().await;
                s.error = Some(format!(
                    "Could not run git: {}. \
                     Install git from https://git-scm.com then relaunch.",
                    e,
                ));
                return;
            }
        }
    }

    // Kill any leftover server on our port from a previous run
    {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(2))
            .build()
            .unwrap();
        if client
            .get(format!("http://127.0.0.1:{}/health", JARVIS_PORT))
            .send()
            .await
            .is_ok()
        {
            // Something is already listening — try to kill it
            #[cfg(unix)]
            {
                let _ = tokio::process::Command::new("fuser")
                    .args(["-k", &format!("{}/tcp", JARVIS_PORT)])
                    .output()
                    .await;
                tokio::time::sleep(Duration::from_secs(2)).await;
            }
            #[cfg(target_os = "windows")]
            {
                // Find the PID holding the port via netstat, then kill it
                if let Ok(output) = tokio::process::Command::new("cmd")
                    .args(["/C", &format!(
                        "for /f \"tokens=5\" %a in ('netstat -ano ^| findstr :{port} ^| findstr LISTENING') do taskkill /PID %a /F",
                        port = JARVIS_PORT,
                    )])
                    .output()
                    .await
                {
                    let _ = output; // best-effort
                }
                tokio::time::sleep(Duration::from_secs(2)).await;
            }
        }
    }

    let startup_model = STARTUP_MODEL;

    let root = project_root.as_ref().unwrap();

    // Install dependencies automatically (handles fresh clones). Skip when the
    // venv already has the jarvis entry point: `uv sync` costs ~70s on every
    // launch but only does work on a fresh clone or a deps change. Normal
    // launches reuse the existing venv. Set OPENJARVIS_FORCE_SYNC=1 to override
    // (e.g. after manually pulling new deps).
    #[cfg(target_os = "windows")]
    let jarvis_entry = root.join(".venv").join("Scripts").join("jarvis.exe");
    #[cfg(not(target_os = "windows"))]
    let jarvis_entry = root.join(".venv").join("bin").join("jarvis");
    let force_sync = std::env::var("OPENJARVIS_FORCE_SYNC")
        .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
        .unwrap_or(false);
    if force_sync || !jarvis_entry.exists() {
        {
            let mut s = status.lock().await;
            s.detail = "Installing dependencies...".into();
        }
        let mut sync_cmd = tokio::process::Command::new(&uv_bin);
        configure_uv_command(&mut sync_cmd);
        hide_command_window(&mut sync_cmd);
        let _ = sync_cmd
            .args([
                "sync",
                // --inexact: do NOT remove packages outside the lock. Without it,
                // every app launch stripped pip from the venv (the recurring
                // "pip vanished" failure, root-caused 2026-06-13).
                "--inexact",
                "--extra",
                "server",
                "--extra",
                "inference-litellm",
                "--extra",
                "inference-cloud",
                "--extra",
                "inference-google",
            ])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .current_dir(root)
            .status()
            .await;
    } else {
        let mut s = status.lock().await;
        s.detail = "Dependencies present — skipping sync.".into();
    }

    // Warm the local Qwen lane in the background. Jarvis must come online even
    // if WSL port forwarding or model load is slow, otherwise Studio appears
    // offline while the model is still warming.
    {
        let qwen_root = root.to_path_buf();
        let qwen_backend = backend.clone();
        let qwen_status = status.clone();
        tauri::async_runtime::spawn(async move {
            let qwen_ok = start_qwen_fast_lane(&qwen_root, qwen_backend, qwen_status.clone()).await;
            if !qwen_ok {
                let mut s = qwen_status.lock().await;
                s.detail =
                    "Qwen fast lane did not become ready; Ollama fallback remains available."
                        .into();
            }
        });
    }

    let litellm_ok = start_litellm_proxy(root, &uv_bin, backend.clone(), status.clone()).await;
    if !litellm_ok {
        let mut s = status.lock().await;
        s.detail =
            "LiteLLM proxy did not become ready; direct Ollama fallback remains available.".into();
    }

    {
        let mut s = status.lock().await;
        s.detail = format!(
            "Starting server with {} from {}...",
            startup_model,
            root.display(),
        );
    }

    let mut cmd = tokio::process::Command::new(&uv_bin);
    configure_uv_command(&mut cmd);
    cmd.args([
        "run",
        "--no-sync",
        "jarvis",
        "serve",
        "--port",
        &JARVIS_PORT.to_string(),
        "--model",
        startup_model,
        // orchestrator (tool-using), not "simple": "simple" has accepts_tools=False,
        // so the chat could never run the agentic loop (file_edit/diffs/preview/etc).
        "--agent",
        "orchestrator",
    ])
    .stdout(std::process::Stdio::null())
    .stderr(std::process::Stdio::piped())
    .current_dir(root);
    hide_command_window(&mut cmd);

    // Inject cloud API keys from ~/.openjarvis/cloud-keys.env
    let cloud_keys = read_cloud_keys();
    let has_cloud_keys = !cloud_keys.is_empty();
    for (key, value) in cloud_keys {
        cmd.env(&key, &value);
    }
    // Escalation safety: cloud escalation on a streaming chat can wedge the WHOLE
    // backend — an aborted request leaves the agent thread running (Python can't
    // cancel threads), and if the cloud call hangs (stale/inherited OPENAI_API_KEY)
    // those threads exhaust the pool and every endpoint locks up. Force escalation
    // OFF unless the operator has explicitly configured a cloud-keys.env. This also
    // overrides any stale OPENJARVIS_ESCALATION=1 inherited from the global env.
    cmd.env("OPENJARVIS_ESCALATION", if has_cloud_keys { "1" } else { "0" });
    // Don't let an inherited global cloud key silently re-enable cloud paths when
    // the operator hasn't opted in via cloud-keys.env.
    if !has_cloud_keys {
        cmd.env("OPENAI_API_KEY", "");
        cmd.env("ANTHROPIC_API_KEY", "");
        cmd.env("GEMINI_API_KEY", "");
        cmd.env("GOOGLE_API_KEY", "");
    }
    let jarvis_child = cmd.spawn();

    match jarvis_child {
        Ok(child) => {
            backend.lock().await.jarvis = Some(ChildHandle { child });
        }
        Err(e) => {
            let mut s = status.lock().await;
            s.error = Some(format!(
                "Could not start jarvis server: {}. \
                 Make sure uv is installed (https://astral.sh/uv) and the OpenJarvis repo is cloned at {}",
                e,
                root.display(),
            ));
            return;
        }
    }

    let server_url = format!("http://127.0.0.1:{}/health", JARVIS_PORT);
    let server_ok = wait_for_url(&server_url, Duration::from_secs(600)).await;

    if !server_ok {
        // Try to read stderr from the failed process for a useful error
        let mut stderr_msg = String::new();
        {
            let mut mgr = backend.lock().await;
            if let Some(ref mut h) = mgr.jarvis {
                if let Some(ref mut stderr) = h.child.stderr.take() {
                    use tokio::io::AsyncReadExt;
                    let mut buf = vec![0u8; 4096];
                    if let Ok(n) = stderr.read(&mut buf).await {
                        stderr_msg = String::from_utf8_lossy(&buf[..n]).to_string();
                    }
                }
            }
        }
        let detail = if stderr_msg.is_empty() {
            format!(
                "Jarvis server did not start. Check that:\n\
                 1. uv is installed ({})\n\
                 2. The OpenJarvis repo is at {}\n\
                 3. Run 'uv sync' in that directory",
                uv_bin,
                root.display(),
            )
        } else {
            format!("Server failed to start: {}", stderr_msg.trim())
        };
        let mut s = status.lock().await;
        s.error = Some(detail);
        return;
    }

    {
        let mut s = status.lock().await;
        s.server_ready = true;
        s.phase = "ready".into();
        s.detail = "All systems ready.".into();
    }

    // Do not auto-pull model variants at startup. Jarvis is intentionally
    // constrained to Qwen 3.6 27B locally plus the remote 35B-A3B worker route.
}

// ---------------------------------------------------------------------------
// Tauri commands
// ---------------------------------------------------------------------------

fn api_base() -> String {
    format!("http://127.0.0.1:{}", JARVIS_PORT)
}

#[tauri::command]
async fn get_setup_status(state: tauri::State<'_, SharedStatus>) -> Result<SetupStatus, String> {
    Ok(state.lock().await.clone())
}

#[tauri::command]
fn get_api_base() -> String {
    api_base()
}

#[tauri::command]
async fn start_backend(
    backend: tauri::State<'_, SharedBackend>,
    status: tauri::State<'_, SharedStatus>,
) -> Result<(), String> {
    let b = backend.inner().clone();
    let s = status.inner().clone();
    tauri::async_runtime::spawn(boot_backend(b, s));
    Ok(())
}

#[tauri::command]
async fn stop_backend(backend: tauri::State<'_, SharedBackend>) -> Result<(), String> {
    backend.lock().await.stop_all_runtime_processes().await;
    Ok(())
}

#[tauri::command]
async fn game_mode_park(backend: tauri::State<'_, SharedBackend>) -> Result<(), String> {
    // Stop our managed children first, then hand off to the canonical park
    // script: it disables the (app-gated) watchdog, kills stray stack
    // processes from other launchers, and shuts down WSL to free the ~22GB
    // of VRAM the Qwen lane holds.
    backend.lock().await.stop_all_runtime_processes().await;
    let root = find_project_root().ok_or("OpenJarvis project root not found")?;
    let script = root.join("scripts").join("jarvis-park.ps1");
    if !script.exists() {
        return Err(format!("park script missing: {}", script.display()));
    }
    let mut cmd = tokio::process::Command::new("powershell");
    cmd.args([
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        &script.to_string_lossy(),
    ]);
    hide_command_window(&mut cmd);
    let status = cmd.status().await.map_err(|e| e.to_string())?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("park script exited with {:?}", status.code()))
    }
}

#[tauri::command]
async fn game_mode_resume() -> Result<(), String> {
    // Bring Jarvis back without closing the app: the canonical resume script
    // re-enables the app-gated watchdog and starts the JarvisStudioStack, then
    // waits for the backend on 7710. Runs from the still-alive Tauri shell.
    let root = find_project_root().ok_or("OpenJarvis project root not found")?;
    let script = root.join("scripts").join("jarvis-resume.ps1");
    if !script.exists() {
        return Err(format!("resume script missing: {}", script.display()));
    }
    let mut cmd = tokio::process::Command::new("powershell");
    cmd.args([
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        &script.to_string_lossy(),
    ]);
    hide_command_window(&mut cmd);
    let status = cmd.status().await.map_err(|e| e.to_string())?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("resume script exited with {:?}", status.code()))
    }
}

#[tauri::command]
async fn check_health(api_url: String) -> Result<serde_json::Value, String> {
    let base = if api_url.is_empty() {
        api_base()
    } else {
        api_url
    };
    let client = reqwest::Client::new();
    let studio_url = format!("{}/studio/ping", base);
    if let Ok(resp) = client.get(&studio_url).send().await {
        if resp.status().is_success() {
            return resp
                .json()
                .await
                .map_err(|e| format!("Invalid response: {}", e));
        }
    }
    let health_url = format!("{}/health", base);
    let resp = client
        .get(&health_url)
        .send()
        .await
        .map_err(|e| format!("Connection failed: {}", e))?;
    resp.json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))
}

#[tauri::command]
async fn fetch_energy(api_url: String) -> Result<serde_json::Value, String> {
    let base = if api_url.is_empty() {
        api_base()
    } else {
        api_url
    };
    let resp = reqwest::get(format!("{}/v1/telemetry/energy", base))
        .await
        .map_err(|e| format!("Connection failed: {}", e))?;
    resp.json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))
}

#[tauri::command]
async fn fetch_telemetry(api_url: String) -> Result<serde_json::Value, String> {
    let base = if api_url.is_empty() {
        api_base()
    } else {
        api_url
    };
    let resp = reqwest::get(format!("{}/v1/telemetry/stats", base))
        .await
        .map_err(|e| format!("Connection failed: {}", e))?;
    resp.json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))
}

#[tauri::command]
async fn fetch_traces(api_url: String, limit: u32) -> Result<serde_json::Value, String> {
    let base = if api_url.is_empty() {
        api_base()
    } else {
        api_url
    };
    let resp = reqwest::get(format!("{}/v1/traces?limit={}", base, limit))
        .await
        .map_err(|e| format!("Connection failed: {}", e))?;
    resp.json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))
}

#[tauri::command]
async fn fetch_trace(api_url: String, trace_id: String) -> Result<serde_json::Value, String> {
    let base = if api_url.is_empty() {
        api_base()
    } else {
        api_url
    };
    let resp = reqwest::get(format!("{}/v1/traces/{}", base, trace_id))
        .await
        .map_err(|e| format!("Connection failed: {}", e))?;
    resp.json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))
}

#[tauri::command]
async fn fetch_learning_stats(api_url: String) -> Result<serde_json::Value, String> {
    let base = if api_url.is_empty() {
        api_base()
    } else {
        api_url
    };
    let resp = reqwest::get(format!("{}/v1/learning/stats", base))
        .await
        .map_err(|e| format!("Connection failed: {}", e))?;
    resp.json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))
}

#[tauri::command]
async fn fetch_learning_policy(api_url: String) -> Result<serde_json::Value, String> {
    let base = if api_url.is_empty() {
        api_base()
    } else {
        api_url
    };
    let resp = reqwest::get(format!("{}/v1/learning/policy", base))
        .await
        .map_err(|e| format!("Connection failed: {}", e))?;
    resp.json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))
}

#[tauri::command]
async fn fetch_memory_stats(api_url: String) -> Result<serde_json::Value, String> {
    let base = if api_url.is_empty() {
        api_base()
    } else {
        api_url
    };
    let resp = reqwest::get(format!("{}/v1/memory/stats", base))
        .await
        .map_err(|e| format!("Connection failed: {}", e))?;
    resp.json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))
}

#[tauri::command]
async fn search_memory(
    api_url: String,
    query: String,
    top_k: u32,
) -> Result<serde_json::Value, String> {
    let base = if api_url.is_empty() {
        api_base()
    } else {
        api_url
    };
    let client = reqwest::Client::new();
    let resp = client
        .post(format!("{}/v1/memory/search", base))
        .json(&serde_json::json!({"query": query, "top_k": top_k}))
        .send()
        .await
        .map_err(|e| format!("Connection failed: {}", e))?;
    resp.json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))
}

#[tauri::command]
async fn fetch_agents(api_url: String) -> Result<serde_json::Value, String> {
    let base = if api_url.is_empty() {
        api_base()
    } else {
        api_url
    };
    let resp = reqwest::get(format!("{}/v1/agents", base))
        .await
        .map_err(|e| format!("Connection failed: {}", e))?;
    resp.json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))
}

#[tauri::command]
async fn fetch_models(api_url: String) -> Result<serde_json::Value, String> {
    let base = if api_url.is_empty() {
        api_base()
    } else {
        api_url
    };
    let resp = reqwest::get(format!("{}/v1/models", base))
        .await
        .map_err(|e| format!("Connection failed: {}", e))?;
    resp.json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))
}

#[tauri::command]
async fn run_jarvis_command(args: Vec<String>) -> Result<String, String> {
    let mut cmd_args = vec![
        "run".to_string(),
        "--no-sync".to_string(),
        "jarvis".to_string(),
    ];
    cmd_args.extend(args);
    let uv_bin = resolve_bin("uv");
    let mut cmd = tokio::process::Command::new(&uv_bin);
    configure_uv_command(&mut cmd);
    let output = cmd
        .args(&cmd_args)
        .output()
        .await
        .map_err(|e| format!("Failed to launch jarvis: {}", e))?;

    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    } else {
        Err(String::from_utf8_lossy(&output.stderr).to_string())
    }
}

#[tauri::command]
async fn fetch_savings(api_url: String) -> Result<serde_json::Value, String> {
    let base = if api_url.is_empty() {
        api_base()
    } else {
        api_url
    };
    let resp = reqwest::get(format!("{}/v1/savings", base))
        .await
        .map_err(|e| format!("Connection failed: {}", e))?;
    resp.json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))
}

/// Transcribe audio via the speech API endpoint.
#[tauri::command]
async fn transcribe_audio(
    api_url: String,
    audio_data: Vec<u8>,
    filename: String,
) -> Result<serde_json::Value, String> {
    let url = format!("{}/v1/speech/transcribe", api_url);
    let client = reqwest::Client::new();

    let part = reqwest::multipart::Part::bytes(audio_data)
        .file_name(filename)
        .mime_str("audio/webm")
        .map_err(|e| format!("Failed to create multipart: {}", e))?;

    let form = reqwest::multipart::Form::new().part("file", part);

    let resp = client
        .post(&url)
        .multipart(form)
        .send()
        .await
        .map_err(|e| format!("Connection failed: {}", e))?;
    let body: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))?;
    Ok(body)
}

/// Submit savings to Supabase leaderboard.
#[tauri::command]
async fn submit_savings(
    supabase_url: String,
    supabase_key: String,
    payload: serde_json::Value,
) -> Result<bool, String> {
    if supabase_url.is_empty() || supabase_key.is_empty() {
        return Ok(false);
    }
    let client = reqwest::Client::new();
    let resp = client
        .post(format!(
            "{}/rest/v1/savings_entries?on_conflict=anon_id",
            supabase_url
        ))
        .header("Content-Type", "application/json")
        .header("apikey", &supabase_key)
        .header("Authorization", format!("Bearer {}", supabase_key))
        .header("Prefer", "resolution=merge-duplicates")
        .json(&payload)
        .send()
        .await
        .map_err(|e| format!("Supabase POST failed: {}", e))?;
    Ok(resp.status().is_success())
}

// ---------------------------------------------------------------------------
// Cloud API key management
// ---------------------------------------------------------------------------

/// Path to the cloud keys file (~/.openjarvis/cloud-keys.env).
fn cloud_keys_path() -> std::path::PathBuf {
    let home = home_dir();
    std::path::PathBuf::from(home)
        .join(".openjarvis")
        .join("cloud-keys.env")
}

/// Read cloud keys from disk and return as key=value pairs.
fn read_cloud_keys() -> Vec<(String, String)> {
    let path = cloud_keys_path();
    let mut keys = Vec::new();
    if let Ok(contents) = std::fs::read_to_string(&path) {
        for line in contents.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            if let Some((k, v)) = line.split_once('=') {
                keys.push((k.trim().to_string(), v.trim().to_string()));
            }
        }
    }
    keys
}

/// Save a single cloud API key to the keys file.
#[tauri::command]
async fn save_cloud_key(key_name: String, key_value: String) -> Result<(), String> {
    let path = cloud_keys_path();
    // Ensure directory exists
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }

    // Read existing keys, update/add the one being saved
    let mut keys: Vec<(String, String)> = read_cloud_keys()
        .into_iter()
        .filter(|(k, _)| k != &key_name)
        .collect();
    if !key_value.is_empty() {
        keys.push((key_name, key_value));
    }

    // Write back
    let content: String = keys
        .iter()
        .map(|(k, v)| format!("{}={}", k, v))
        .collect::<Vec<_>>()
        .join("\n");
    std::fs::write(&path, content + "\n").map_err(|e| format!("Failed to save key: {}", e))?;

    // Set permissions to owner-only (chmod 600)
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }

    // Tell the running server to hot-reload its cloud engine so the user
    // doesn't need to restart the app after entering an API key.
    let reload_url = format!("http://127.0.0.1:{}/v1/cloud/reload", JARVIS_PORT);
    let _ = reqwest::Client::new()
        .post(&reload_url)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await;

    Ok(())
}

/// Get which cloud providers have keys configured (without exposing values).
#[tauri::command]
async fn get_cloud_key_status() -> Result<serde_json::Value, String> {
    let keys = read_cloud_keys();
    let status: Vec<serde_json::Value> = keys
        .iter()
        .map(|(k, v)| serde_json::json!({ "key": k, "set": !v.is_empty() }))
        .collect();
    Ok(serde_json::json!(status))
}

/// Pull a model via Ollama (called from frontend download button).
#[tauri::command]
async fn pull_ollama_model(model_name: String) -> Result<serde_json::Value, String> {
    pull_model(&model_name)
        .await
        .map_err(|e| format!("Failed to pull {}: {}", model_name, e))?;
    Ok(serde_json::json!({"status": "ok", "model": model_name}))
}

/// Delete a model from Ollama.
#[tauri::command]
async fn delete_ollama_model(model_name: String) -> Result<serde_json::Value, String> {
    let url = format!("http://127.0.0.1:{}/api/delete", OLLAMA_PORT);
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client
        .delete(&url)
        .json(&serde_json::json!({"name": model_name}))
        .send()
        .await
        .map_err(|e| format!("Delete failed: {}", e))?;
    if !resp.status().is_success() {
        return Err(format!("Delete returned status {}", resp.status()));
    }
    Ok(serde_json::json!({"status": "deleted", "model": model_name}))
}

/// Check speech backend health.
#[tauri::command]
async fn speech_health(api_url: String) -> Result<serde_json::Value, String> {
    let url = format!("{}/v1/speech/health", api_url);
    let resp = reqwest::get(&url)
        .await
        .map_err(|e| format!("Connection failed: {}", e))?;
    let body: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))?;
    Ok(body)
}

// ---------------------------------------------------------------------------
// Native macOS overlay — NSPanel + WKWebView, entirely bypassing Tauri's
// window management so we get proper always-on-top, transparency, non-
// activating panel behaviour and cross-Space support.
// ---------------------------------------------------------------------------

#[cfg(target_os = "macos")]
mod native_overlay {
    use objc::declare::ClassDecl;
    use objc::runtime::{Class, Object, Sel, BOOL, NO, YES};
    use objc::{class, msg_send, sel, sel_impl};
    use std::sync::atomic::{AtomicUsize, Ordering};

    /// Raw pointer to the NSPanel, stored as usize for atomicity.
    static PANEL_PTR: AtomicUsize = AtomicUsize::new(0);
    /// Raw pointer to the WKWebView inside the panel.
    static WEBVIEW_PTR: AtomicUsize = AtomicUsize::new(0);
    /// Raw pointer to the previously-frontmost NSRunningApplication.
    static PREV_APP: AtomicUsize = AtomicUsize::new(0);

    // CoreGraphics geometry types expected by AppKit.
    #[repr(C)]
    #[derive(Copy, Clone)]
    struct CGPoint {
        x: f64,
        y: f64,
    }
    #[repr(C)]
    #[derive(Copy, Clone)]
    struct CGSize {
        width: f64,
        height: f64,
    }
    #[repr(C)]
    #[derive(Copy, Clone)]
    struct CGRect {
        origin: CGPoint,
        size: CGSize,
    }

    /// Create an autoreleased NSString from a Rust &str.
    unsafe fn nsstring(s: &str) -> *mut Object {
        let obj: *mut Object = msg_send![class!(NSString), alloc];
        msg_send![obj,
            initWithBytes: s.as_ptr()
            length: s.len()
            encoding: 4usize  // NSUTF8StringEncoding
        ]
    }

    // ------------------------------------------------------------------
    // Conversation persistence
    // ------------------------------------------------------------------

    fn conversation_path() -> std::path::PathBuf {
        std::path::PathBuf::from(super::home_dir())
            .join(".openjarvis")
            .join("overlay-conversation.json")
    }

    pub fn load_conversation() -> String {
        std::fs::read_to_string(conversation_path()).unwrap_or_else(|_| "[]".into())
    }

    /// Read cloud API keys and return a JSON array of model IDs
    /// whose provider has a key configured.
    fn cloud_models_json() -> String {
        let keys = super::read_cloud_keys();
        let mut models: Vec<&str> = Vec::new();
        for (name, value) in &keys {
            if value.is_empty() {
                continue;
            }
            match name.as_str() {
                "OPENAI_API_KEY" => models.extend(["gpt-4o", "gpt-4o-mini"]),
                "ANTHROPIC_API_KEY" => {
                    models.extend(["claude-sonnet-4-20250514", "claude-haiku-4-20250414"])
                }
                "GEMINI_API_KEY" | "GOOGLE_API_KEY" => {
                    models.extend(["gemini-2.5-flash", "gemini-2.5-pro"])
                }
                _ => {}
            }
        }
        serde_json::to_string(&models).unwrap_or_else(|_| "[]".into())
    }

    fn save_conversation(json: &str) {
        let path = conversation_path();
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let _ = std::fs::write(&path, json);
    }

    /// Apply every transparency trick to the WKWebView.
    /// Called once at creation and again after the page finishes loading.
    unsafe fn force_transparent(wv: *mut Object) {
        let clear: *mut Object = msg_send![class!(NSColor), clearColor];
        let _: () = msg_send![wv, _setDrawsBackground: NO];
        let no_num: *mut Object = msg_send![class!(NSNumber), numberWithBool: NO];
        let _: () = msg_send![wv, setValue: no_num forKey: nsstring("drawsBackground")];
        let _: () = msg_send![wv, setUnderPageBackgroundColor: clear];
        // Also inject CSS to nuke any remaining background
        let js = nsstring(
            "document.documentElement.style.background='transparent';\
             document.body.style.background='transparent';",
        );
        let nil: *mut Object = std::ptr::null_mut();
        let _: () = msg_send![wv, evaluateJavaScript: js completionHandler: nil];
    }

    // ------------------------------------------------------------------
    // Public API (must be called on the main thread)
    // ------------------------------------------------------------------

    /// Build the native overlay panel.  Call once during app setup.
    pub unsafe fn create(html: &str, api_port: u16) {
        // --- Custom NSPanel subclass that accepts keyboard input ------
        if Class::get("JarvisOverlayPanel").is_none() {
            let sup = Class::get("NSPanel").unwrap();
            let mut decl = ClassDecl::new("JarvisOverlayPanel", sup).unwrap();
            extern "C" fn yes(_: &Object, _: Sel) -> BOOL {
                YES
            }
            decl.add_method(
                sel!(canBecomeKeyWindow),
                yes as extern "C" fn(&Object, Sel) -> BOOL,
            );
            decl.register();
        }

        // --- WKNavigationDelegate — re-apply transparency after load --
        if Class::get("JarvisOverlayNavDelegate").is_none() {
            let sup = Class::get("NSObject").unwrap();
            let mut decl = ClassDecl::new("JarvisOverlayNavDelegate", sup).unwrap();
            extern "C" fn did_finish(_: &Object, _: Sel, wv: *mut Object, _nav: *mut Object) {
                unsafe {
                    force_transparent(wv);
                }
            }
            decl.add_method(
                sel!(webView:didFinishNavigation:),
                did_finish as extern "C" fn(&Object, Sel, *mut Object, *mut Object),
            );
            decl.register();
        }

        // --- WKScriptMessageHandler so JS can call hide() ------------
        if Class::get("JarvisOverlayMsgHandler").is_none() {
            let sup = Class::get("NSObject").unwrap();
            let mut decl = ClassDecl::new("JarvisOverlayMsgHandler", sup).unwrap();
            extern "C" fn on_msg(_: &Object, _: Sel, _ctrl: *mut Object, msg: *mut Object) {
                unsafe {
                    let body: *mut Object = msg_send![msg, body];
                    if body.is_null() {
                        return;
                    }
                    let c: *const std::os::raw::c_char = msg_send![body, UTF8String];
                    if c.is_null() {
                        return;
                    }
                    if let Ok(s) = std::ffi::CStr::from_ptr(c).to_str() {
                        if s == "hide" {
                            hide();
                        } else if let Some(json) = s.strip_prefix("save:") {
                            save_conversation(json);
                        } else if let Some(coords) = s.strip_prefix("drag:") {
                            drag(coords);
                        }
                    }
                }
            }
            decl.add_method(
                sel!(userContentController:didReceiveScriptMessage:),
                on_msg as extern "C" fn(&Object, Sel, *mut Object, *mut Object),
            );
            decl.register();
        }

        // --- Create the NSPanel --------------------------------------
        let frame = CGRect {
            origin: CGPoint { x: 0.0, y: 0.0 },
            size: CGSize {
                width: 560.0,
                height: 400.0,
            },
        };
        // NSWindowStyleMaskNonactivatingPanel = 1 << 7
        let style: u64 = 1 << 7;

        let cls = Class::get("JarvisOverlayPanel").unwrap();
        let panel: *mut Object = msg_send![cls, alloc];
        let panel: *mut Object = msg_send![panel,
            initWithContentRect: frame
            styleMask: style
            backing: 2u64       // NSBackingStoreBuffered
            defer: NO
        ];

        // Window level — NSFloatingWindowLevel (3).
        let _: () = msg_send![panel, setLevel: 3_i64];
        // canJoinAllSpaces (1) | fullScreenAuxiliary (1<<8)
        let _: () = msg_send![panel, setCollectionBehavior: 257_u64];
        let _: () = msg_send![panel, setHidesOnDeactivate: NO];
        let _: () = msg_send![panel, setOpaque: NO];
        let _: () = msg_send![panel, setHasShadow: NO];
        let _: () = msg_send![panel, setMovableByWindowBackground: YES];

        let clear: *mut Object = msg_send![class!(NSColor), clearColor];
        let _: () = msg_send![panel, setBackgroundColor: clear];
        let _: () = msg_send![panel, center];

        // --- WKWebView -----------------------------------------------
        let cfg: *mut Object = msg_send![class!(WKWebViewConfiguration), alloc];
        let cfg: *mut Object = msg_send![cfg, init];

        // Attach message handler ("overlay" channel)
        let hcls = Class::get("JarvisOverlayMsgHandler").unwrap();
        let handler: *mut Object = msg_send![hcls, alloc];
        let handler: *mut Object = msg_send![handler, init];
        let uc: *mut Object = msg_send![cfg, userContentController];
        let _: () = msg_send![uc,
            addScriptMessageHandler: handler
            name: nsstring("overlay")
        ];

        let wv: *mut Object = msg_send![class!(WKWebView), alloc];
        let wv: *mut Object = msg_send![wv,
            initWithFrame: frame
            configuration: cfg
        ];

        // ---- Make the webview fully transparent ----
        force_transparent(wv);

        // Set navigation delegate so we re-apply after page loads
        let nav_cls = Class::get("JarvisOverlayNavDelegate").unwrap();
        let nav_del: *mut Object = msg_send![nav_cls, alloc];
        let nav_del: *mut Object = msg_send![nav_del, init];
        let _: () = msg_send![wv, setNavigationDelegate: nav_del];

        let _: () = msg_send![panel, setContentView: wv];
        WEBVIEW_PTR.store(wv as usize, Ordering::SeqCst);

        // Inject saved conversation into the HTML template, then load it.
        // Use the API server as the base URL so fetch() is same-origin.
        // Escape "</" so the JSON can't prematurely close the <script> tag.
        // ("\/" is valid JSON — resolves back to "/" when parsed.)
        let saved = load_conversation().replace("</", "<\\/");
        let cloud = cloud_models_json();
        let filled = html
            .replace("__SAVED_MESSAGES__", &saved)
            .replace("__CLOUD_MODELS__", &cloud);
        let base_str = nsstring(&format!("http://127.0.0.1:{}", api_port));
        let base_url: *mut Object = msg_send![class!(NSURL), URLWithString: base_str];
        let _: () = msg_send![wv,
            loadHTMLString: nsstring(&filled)
            baseURL: base_url
        ];

        PANEL_PTR.store(panel as usize, Ordering::SeqCst);
    }

    pub unsafe fn toggle() {
        let ptr = PANEL_PTR.load(Ordering::SeqCst);
        if ptr == 0 {
            return;
        }
        let panel = ptr as *mut Object;
        let vis: BOOL = msg_send![panel, isVisible];
        if vis != NO {
            hide();
        } else {
            show();
        }
    }

    pub unsafe fn show() {
        let ptr = PANEL_PTR.load(Ordering::SeqCst);
        if ptr == 0 {
            return;
        }
        let panel = ptr as *mut Object;

        // Re-apply transparency every time (the webview can reset it)
        let wv_ptr = WEBVIEW_PTR.load(Ordering::SeqCst);
        if wv_ptr != 0 {
            force_transparent(wv_ptr as *mut Object);
        }

        // Remember the currently-frontmost app so we can restore it.
        let ws: *mut Object = msg_send![class!(NSWorkspace), sharedWorkspace];
        let front: *mut Object = msg_send![ws, frontmostApplication];
        if !front.is_null() {
            let _: () = msg_send![front, retain];
            let old = PREV_APP.swap(front as usize, Ordering::SeqCst);
            if old != 0 {
                let _: () = msg_send![(old as *mut Object), release];
            }
        }

        // Activate our process so the panel receives keyboard input.
        let app: *mut Object = msg_send![class!(NSApplication), sharedApplication];
        let _: () = msg_send![app, activateIgnoringOtherApps: YES];
        let nil: *mut Object = std::ptr::null_mut();
        let _: () = msg_send![panel, makeKeyAndOrderFront: nil];

        // Focus the text field inside the webview.
        let wv: *mut Object = msg_send![panel, contentView];
        let js = nsstring("document.getElementById('input').focus()");
        let _: () = msg_send![wv, evaluateJavaScript: js completionHandler: nil];
    }

    /// Move the panel by a screen-space delta (called from JS drag handler).
    unsafe fn drag(coords: &str) {
        let ptr = PANEL_PTR.load(Ordering::SeqCst);
        if ptr == 0 {
            return;
        }
        let panel = ptr as *mut Object;
        let Some((dxs, dys)) = coords.split_once(',') else {
            return;
        };
        let Ok(dx) = dxs.parse::<f64>() else { return };
        let Ok(dy) = dys.parse::<f64>() else { return };
        // NSWindow frame origin is bottom-left; screen Y increases upward,
        // but mouse screenY increases downward, so invert dy.
        let frame: CGRect = msg_send![panel, frame];
        let origin = CGPoint {
            x: frame.origin.x + dx,
            y: frame.origin.y - dy,
        };
        let _: () = msg_send![panel, setFrameOrigin: origin];
    }

    pub unsafe fn hide() {
        let ptr = PANEL_PTR.load(Ordering::SeqCst);
        if ptr == 0 {
            return;
        }
        let panel = ptr as *mut Object;
        let nil: *mut Object = std::ptr::null_mut();
        let _: () = msg_send![panel, orderOut: nil];

        // Give focus back to whatever app was frontmost before.
        let prev = PREV_APP.swap(0, Ordering::SeqCst);
        if prev != 0 {
            let prev_app = prev as *mut Object;
            let _: BOOL = msg_send![prev_app, activateWithOptions: 2_u64];
            let _: () = msg_send![prev_app, release];
        }
    }
}

/// Dispatch a closure onto the main thread via GCD.
#[cfg(target_os = "macos")]
fn on_main_thread(f: impl FnOnce() + Send + 'static) {
    dispatch::Queue::main().exec_async(f);
}

// ---------------------------------------------------------------------------
// Overlay Tauri commands (thin wrappers that dispatch to the main thread)
// ---------------------------------------------------------------------------

#[tauri::command]
async fn get_overlay_conversation() -> Result<String, String> {
    #[cfg(target_os = "macos")]
    {
        return Ok(native_overlay::load_conversation());
    }
    #[cfg(not(target_os = "macos"))]
    Ok("[]".into())
}

#[tauri::command]
async fn toggle_overlay() -> Result<(), String> {
    #[cfg(target_os = "macos")]
    on_main_thread(|| unsafe { native_overlay::toggle() });
    Ok(())
}

#[tauri::command]
async fn hide_overlay() -> Result<(), String> {
    #[cfg(target_os = "macos")]
    on_main_thread(|| unsafe { native_overlay::hide() });
    Ok(())
}

// ---------------------------------------------------------------------------
// App entry point
// ---------------------------------------------------------------------------

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let backend: SharedBackend = Arc::new(Mutex::new(BackendManager::default()));
    let status: SharedStatus = Arc::new(Mutex::new(SetupStatus::default()));

    let boot_backend_ref = backend.clone();
    let boot_status_ref = status.clone();

    tauri::Builder::default()
        .manage(backend.clone())
        .manage(status.clone())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec!["--hidden"]),
        ))
        // .plugin(tauri_plugin_updater::Builder::new().build()) // disabled for local dev
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_focus();
            }
        }))
        .setup(move |app| {
            // System tray
            let show = MenuItemBuilder::with_id("show", "Show / Hide").build(app)?;
            let health = MenuItemBuilder::with_id("health", "Health: starting...")
                .enabled(false)
                .build(app)?;
            let quit = MenuItemBuilder::with_id("quit", "Quit OpenJarvis").build(app)?;

            let menu = MenuBuilder::new(app)
                .item(&show)
                .separator()
                .item(&health)
                .separator()
                .item(&quit)
                .build()?;

            let _tray = TrayIconBuilder::with_id("main")
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("OpenJarvis")
                .menu(&menu)
                .on_menu_event(move |app, event| match event.id().as_ref() {
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            if window.is_visible().unwrap_or(false) {
                                let _ = window.hide();
                            } else {
                                let _ = window.show();
                                let _ = window.set_focus();
                            }
                        }
                    }
                    "quit" => {
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            // Create native macOS overlay panel
            #[cfg(target_os = "macos")]
            unsafe {
                native_overlay::create(include_str!("overlay.html"), JARVIS_PORT);
            }

            // Register Cmd+Shift+Space to toggle the overlay
            {
                use tauri_plugin_global_shortcut::{
                    Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState,
                };
                let sc = Shortcut::new(Some(Modifiers::META | Modifiers::SHIFT), Code::Space);
                if let Err(e) = app.global_shortcut().on_shortcut(sc, |_app, _sc, ev| {
                    if ev.state == ShortcutState::Pressed {
                        #[cfg(target_os = "macos")]
                        unsafe {
                            native_overlay::toggle();
                        }
                    }
                }) {
                    eprintln!("Warning: could not register Cmd+Shift+Space: {e}");
                }
            }

            // Auto-start backend services on launch
            tauri::async_runtime::spawn(boot_backend(boot_backend_ref, boot_status_ref));

            // Watchdog runs only while the app is open: enable its scheduled
            // task now, disable it on exit (RunEvent::ExitRequested below).
            set_watchdog_task_enabled(true);

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_setup_status,
            get_api_base,
            start_backend,
            stop_backend,
            game_mode_park,
            game_mode_resume,
            check_health,
            fetch_energy,
            fetch_telemetry,
            fetch_traces,
            fetch_trace,
            fetch_learning_stats,
            fetch_learning_policy,
            fetch_memory_stats,
            search_memory,
            fetch_agents,
            fetch_models,
            run_jarvis_command,
            fetch_savings,
            submit_savings,
            transcribe_audio,
            speech_health,
            pull_ollama_model,
            delete_ollama_model,
            save_cloud_key,
            get_cloud_key_status,
            toggle_overlay,
            hide_overlay,
            get_overlay_conversation,
        ])
        .build(tauri::generate_context!())
        .expect("error while building OpenJarvis Desktop")
        .run(move |_app, event| {
            if let tauri::RunEvent::ExitRequested { .. } = event {
                // App closing → watchdog goes dormant (won't fire while closed).
                set_watchdog_task_enabled(false);
                stop_all_blocking(backend.clone());
            }
        });
}
