param(
    [Parameter(Mandatory = $true)][string]$StudyRoot,
    [string]$SkillRoot = "",
    [string]$ModelName = "",
    [int]$MinAnimationCount = 0,
    [switch]$ExpectFullBodyPreview,
    [int]$Port = 0,
    [switch]$KeepOpen
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "PowerShell 7 or newer is required so local CDP requests can explicitly bypass configured HTTP proxies."
}

if ([string]::IsNullOrWhiteSpace($SkillRoot)) {
    $SkillRoot = Split-Path -Parent $PSScriptRoot
}

function Resolve-ExistingPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$Leaf
    )

    $pathType = if ($Leaf) { "Leaf" } else { "Container" }
    if (-not (Test-Path -LiteralPath $Path -PathType $pathType)) {
        throw "Required path does not exist: $Path"
    }
    return (Resolve-Path -LiteralPath $Path).Path
}

function Join-ConfigPath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Child
    )

    return Join-Path -Path $Root -ChildPath ($Child -replace "/", "\")
}

function Quote-ProcessArgument {
    param(
        [Parameter(Mandatory = $true)][string]$Value
    )

    return '"' + ($Value -replace '"', '\"') + '"'
}

function Get-FreeTcpPort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try {
        return $listener.LocalEndpoint.Port
    }
    finally {
        $listener.Stop()
    }
}

function Stop-NewGameModelViewerProcesses {
    param(
        [int[]]$BeforePids
    )

    for ($attempt = 0; $attempt -lt 12; $attempt++) {
        $targets = @(
            Get-Process -Name "GameModelViewer" -ErrorAction SilentlyContinue |
                Where-Object { $BeforePids -notcontains $_.Id }
        )
        if (-not $targets.Count) {
            return
        }
        $targets | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
}

$skillRootPath = Resolve-ExistingPath -Path $SkillRoot
$studyRootPath = Resolve-ExistingPath -Path $StudyRoot
$configPath = Resolve-ExistingPath -Path (Join-Path -Path $skillRootPath -ChildPath "references\model-viewer-gallery-config.json") -Leaf
$config = Get-Content -LiteralPath $configPath -Encoding UTF8 -Raw | ConvertFrom-Json

$viewerExe = Resolve-ExistingPath -Path (Join-ConfigPath -Root $skillRootPath -Child "$($config.tool.directory)\$($config.tool.exe_name)") -Leaf
$modelsDir = Resolve-ExistingPath -Path (Join-ConfigPath -Root $studyRootPath -Child $config.paths.models_dir)
$previewIndexDir = Resolve-ExistingPath -Path (Join-ConfigPath -Root $studyRootPath -Child $config.paths.preview_index_dir)
$manifestPath = Resolve-ExistingPath -Path (Join-Path -Path $previewIndexDir -ChildPath $config.paths.manifest_name) -Leaf
$thumbsDir = Resolve-ExistingPath -Path (Join-Path -Path $previewIndexDir -ChildPath $config.paths.thumbs_dir)
$previewCacheDir = Join-Path -Path $previewIndexDir -ChildPath $config.paths.preview_cache_dir
if (-not (Test-Path -LiteralPath $previewCacheDir -PathType Container)) {
    New-Item -ItemType Directory -Path $previewCacheDir -Force | Out-Null
}
$previewCacheDir = (Resolve-Path -LiteralPath $previewCacheDir).Path

$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
    throw "Node.js was not found. Install Node.js before running GameModelViewer GUI verification."
}

if ($Port -le 0) {
    $defaultPort = 9228
    $defaultInUse = Get-NetTCPConnection -LocalPort $defaultPort -ErrorAction SilentlyContinue
    $Port = if ($defaultInUse) { Get-FreeTcpPort } else { $defaultPort }
}

$beforeViewerPids = @(Get-Process -Name "GameModelViewer" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
$process = $null
$nodeScriptPath = Join-Path -Path ([System.IO.Path]::GetTempPath()) -ChildPath ("game-model-viewer-cdp-" + [Guid]::NewGuid().ToString("N") + ".mjs")

try {
    $argumentLine = @(
        "--remote-debugging-port=$Port",
        "--study-root", (Quote-ProcessArgument $studyRootPath),
        "--models-dir", (Quote-ProcessArgument $modelsDir),
        "--manifest", (Quote-ProcessArgument $manifestPath),
        "--thumbs-dir", (Quote-ProcessArgument $thumbsDir),
        "--preview-cache-dir", (Quote-ProcessArgument $previewCacheDir)
    ) -join " "

    $process = Start-Process -FilePath $viewerExe -ArgumentList $argumentLine -PassThru -WindowStyle Hidden

    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/json/list" -UseBasicParsing -TimeoutSec 1 -NoProxy
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            $ready = $false
        }
    }
    if (-not $ready) {
        throw "GameModelViewer did not expose a CDP endpoint on port $Port."
    }

    $env:GAME_ANALYSIS_CDP_PORT = [string]$Port
    $env:GAME_ANALYSIS_VERIFY_MODEL_NAME = $ModelName
    $env:GAME_ANALYSIS_VERIFY_MIN_ANIMATION_COUNT = [string]$MinAnimationCount
    $env:GAME_ANALYSIS_EXPECT_FULL_BODY_PREVIEW = if ($ExpectFullBodyPreview) { "1" } else { "0" }

    $nodeScript = @'
const port = Number(process.env.GAME_ANALYSIS_CDP_PORT || "0");
const modelName = process.env.GAME_ANALYSIS_VERIFY_MODEL_NAME || "";
const minAnimationCount = Number(process.env.GAME_ANALYSIS_VERIFY_MIN_ANIMATION_COUNT || "0");
const expectFullBodyPreview = process.env.GAME_ANALYSIS_EXPECT_FULL_BODY_PREVIEW === "1";

const tabs = await fetch(`http://127.0.0.1:${port}/json/list`).then((response) => response.json());
const page = tabs.find((tab) => tab.type === "page" && tab.webSocketDebuggerUrl) || tabs.find((tab) => tab.webSocketDebuggerUrl);
if (!page) throw new Error("No debuggable GameModelViewer page target was found.");

const ws = new WebSocket(page.webSocketDebuggerUrl);
let nextId = 1;
const pending = new Map();

ws.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (message.id && pending.has(message.id)) {
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(JSON.stringify(message.error)));
    else resolve(message.result);
  }
});

await new Promise((resolve, reject) => {
  ws.addEventListener("open", resolve, { once: true });
  ws.addEventListener("error", reject, { once: true });
});

function send(method, params = {}) {
  const id = nextId++;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}

async function evaluate(expression, awaitPromise = false) {
  const result = await send("Runtime.evaluate", {
    expression,
    awaitPromise,
    returnByValue: true,
    userGesture: true
  });
  if (result.exceptionDetails) {
    throw new Error(JSON.stringify(result.exceptionDetails));
  }
  return result.result?.value;
}

await send("Runtime.enable");
await send("Page.enable");

for (let i = 0; i < 80; i += 1) {
  const ready = await evaluate("document.readyState === 'complete' && !!document.querySelector('#filter-text')");
  if (ready) break;
  await new Promise((resolve) => setTimeout(resolve, 250));
}

const result = await evaluate(`(async () => {
  const modelName = ${JSON.stringify(modelName)};
  const minAnimationCount = ${JSON.stringify(minAnimationCount)};
  const expectFullBodyPreview = ${JSON.stringify(expectFullBodyPreview)};
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const input = document.querySelector('#filter-text');
  if (!input) return { ok: false, reason: 'filter input missing', bodyText: document.body?.innerText?.slice(0, 600) || '' };

  if (modelName) {
    input.value = modelName;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await delay(500);
  }

  const cards = Array.from(document.querySelectorAll('.model-card'));
  const card = modelName ? cards.find((element) => element.textContent.includes(modelName)) : cards[0];
  if (!card) {
    return {
      ok: false,
      reason: 'model card missing',
      cardCount: cards.length,
      bodyText: document.body?.innerText?.slice(0, 1200) || ''
    };
  }

  card.click();
  for (let i = 0; i < 120; i += 1) {
    const select = document.querySelector('#animation-select');
    const listCount = document.querySelectorAll('#animation-list .animation-list-button').length;
    const count = Math.max(select ? select.options.length : 0, listCount);
    if (count >= minAnimationCount) break;
    await delay(250);
  }

  const select = document.querySelector('#animation-select');
  const sidebar = document.querySelector('#animation-sidebar');
  const panel = document.querySelector('#animation-panel');
  const listButtons = Array.from(document.querySelectorAll('#animation-list .animation-list-button'));
  const timeline = document.querySelector('#animation-timeline');
  const toggle = document.querySelector('#animation-toggle');
  const speed = document.querySelector('#animation-speed');
  const status = document.querySelector('#animation-status');
  const optionCount = select ? select.options.length : 0;
  const listCount = listButtons.length;
  const visibleAnimationUi = !!(sidebar && !sidebar.hidden && panel && !panel.hidden);
  const initialToggleText = toggle?.textContent?.trim() || '';
  if (minAnimationCount && timeline) {
    const max = Number(timeline.max || '0');
    timeline.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 1 }));
    timeline.value = String(max > 0 ? Math.min(max, Math.max(0.01, max / 2)) : 0);
    timeline.dispatchEvent(new Event('input', { bubbles: true }));
    timeline.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1 }));
    await delay(100);
  }
  const toggleTextAfterScrub = toggle?.textContent?.trim() || '';

  let fullBodyCheck = {
    expected: expectFullBodyPreview,
    buttonVisible: false,
    clicked: false,
    titleAfterClick: '',
    buttonTextAfterClick: '',
    ok: !expectFullBodyPreview
  };
  if (expectFullBodyPreview) {
    const fullBodyButton = document.querySelector('#full-body-preview');
    fullBodyCheck.buttonVisible = !!(fullBodyButton && !fullBodyButton.hidden);
    fullBodyCheck.buttonTextBeforeClick = fullBodyButton?.textContent?.trim() || '';
    if (fullBodyCheck.buttonVisible) {
      fullBodyButton.click();
      fullBodyCheck.clicked = true;
      for (let i = 0; i < 120; i += 1) {
        await delay(250);
        const titleText = document.querySelector('#model-title')?.textContent?.trim() || '';
        const buttonText = fullBodyButton.textContent?.trim() || '';
        const viewportErrorText = document.querySelector('#viewport-error')?.textContent?.trim() || '';
        if (
          titleText &&
          titleText !== modelName &&
          buttonText &&
          buttonText !== fullBodyCheck.buttonTextBeforeClick &&
          !viewportErrorText
        ) break;
      }
    }
    fullBodyCheck.titleAfterClick = document.querySelector('#model-title')?.textContent?.trim() || '';
    fullBodyCheck.buttonTextAfterClick = fullBodyButton?.textContent?.trim() || '';
    fullBodyCheck.viewportErrorAfterClick = document.querySelector('#viewport-error')?.textContent?.trim() || '';
    fullBodyCheck.titleSwitched = Boolean(
      fullBodyCheck.titleAfterClick.includes('全身预览') ||
        (modelName && fullBodyCheck.titleAfterClick && fullBodyCheck.titleAfterClick !== modelName)
    );
    fullBodyCheck.buttonSwitched = Boolean(
      fullBodyCheck.buttonTextAfterClick.includes('单件预览') ||
        (fullBodyCheck.buttonTextBeforeClick &&
          fullBodyCheck.buttonTextAfterClick &&
          fullBodyCheck.buttonTextAfterClick !== fullBodyCheck.buttonTextBeforeClick)
    );
    fullBodyCheck.ok = Boolean(
      fullBodyCheck.buttonVisible &&
        fullBodyCheck.clicked &&
        fullBodyCheck.titleSwitched &&
        fullBodyCheck.buttonSwitched &&
        !fullBodyCheck.viewportErrorAfterClick
    );
  }

  const hasCanvas = document.querySelectorAll('canvas').length > 0;
  const enoughAnimations = !minAnimationCount || listCount >= minAnimationCount;
  const hasTransport = !minAnimationCount || Boolean(visibleAnimationUi && timeline && toggle && speed);
  const scrubPaused = !minAnimationCount || (initialToggleText !== toggleTextAfterScrub && toggleTextAfterScrub.length > 0);
  const ok = hasCanvas && enoughAnimations && hasTransport && scrubPaused && fullBodyCheck.ok;

  return {
    ok,
    reason: ok ? '' : 'model preview controls did not reach expected state',
    selectedTitle: document.querySelector('#model-title')?.textContent?.trim() || '',
    optionCount,
    listCount,
    firstOptions: select ? Array.from(select.options).slice(0, 8).map((option) => option.textContent.trim()) : [],
    firstListItems: listButtons.slice(0, 8).map((button) => button.textContent.trim()),
    animationStatus: status?.textContent?.trim() || '',
    initialToggleText,
    toggleText: toggleTextAfterScrub,
    timelineMax: timeline?.max || '',
    speedValue: speed?.value || '',
    hasAnimationLayout: document.querySelector('#preview-overlay')?.classList.contains('has-animations') || false,
    hasFullBodyPreviewLayout: document.querySelector('#preview-overlay')?.classList.contains('has-full-body-preview') || false,
    sidebarHidden: sidebar ? sidebar.hidden : true,
    panelHidden: panel ? panel.hidden : true,
    checks: { hasCanvas, enoughAnimations, hasTransport, scrubPaused, fullBodyPreview: fullBodyCheck },
    canvasCount: document.querySelectorAll('canvas').length,
    viewportError: document.querySelector('#viewport-error')?.textContent?.trim() || '',
    bodyTextSample: document.body?.innerText?.slice(0, 800) || ''
  };
})()`, true);

ws.close();
console.log(JSON.stringify({ pageTitle: page.title, url: page.url, result }, null, 2));
'@

    Set-Content -LiteralPath $nodeScriptPath -Value $nodeScript -Encoding UTF8
    $output = & $nodeCommand.Source $nodeScriptPath
    if ($LASTEXITCODE -ne 0) {
        throw "Node CDP verification failed with exit code $LASTEXITCODE."
    }

    $payload = $output | ConvertFrom-Json
    if (-not $payload.result.ok) {
        Write-Output $output
        throw "GameModelViewer GUI verification failed: $($payload.result.reason)"
    }

    $output
}
finally {
    Remove-Item -LiteralPath $nodeScriptPath -Force -ErrorAction SilentlyContinue
    if (-not $KeepOpen) {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
        Stop-NewGameModelViewerProcesses -BeforePids $beforeViewerPids
    }
}
