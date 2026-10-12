const api = window.gameVfxViewer;
const list = document.getElementById('list');
const search = document.getElementById('search');
const typeFilter = document.getElementById('typeFilter');
const tagFilter = document.getElementById('tagFilter');
const statusFilter = document.getElementById('statusFilter');
const summary = document.getElementById('summary');
const title = document.getElementById('title');
const subtitle = document.getElementById('subtitle');
const statusLine = document.getElementById('statusLine');
const metadata = document.getElementById('metadata');
const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');

let items = [];
let selected = null;
let selectedPayload = null;
let particles = [];
let texture = null;
let lastTime = performance.now();
let spawnCarry = 0;
let prefabSpawnCarry = [];

function resizeCanvas() {
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(320, Math.floor(rect.width * ratio));
  canvas.height = Math.max(240, Math.floor(rect.height * ratio));
}

function norm(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function constantValue(value, fallback) {
  if (typeof value === 'number') return value;
  if (value && typeof value === 'object') {
    if (typeof value.constant === 'number') return value.constant;
    if (typeof value.constantMin === 'number' && typeof value.constantMax === 'number') {
      return (value.constantMin + value.constantMax) / 2;
    }
  }
  return fallback;
}

function colorString(reconstruction) {
  const color = reconstruction?.modules?.main?.startColor?.color;
  if (typeof color === 'string' && color) return color;
  return 'rgba(255, 210, 120, 0.92)';
}

function currentModules() {
  return selectedPayload?.reconstruction?.modules || {};
}

function tagLabel(tag) {
  return String(tag || '').replace(/^vfx:/, '');
}

function itemTags(item) {
  return Array.isArray(item.tags) ? item.tags : [];
}

function baseName(value) {
  return String(value || '').replace(/\\/g, '/').split('/').pop() || '';
}

function originalPrefabItems(manifest) {
  const exportInfo = manifest?.original_vfx_prefab_export;
  const prefabs = Array.isArray(exportInfo?.prefabs) ? exportInfo.prefabs : [];
  return [...prefabs].sort((left, right) => {
    const byCount = norm(right.particle_system_count, 0) - norm(left.particle_system_count, 0);
    if (byCount !== 0) return byCount;
    return String(left.unity_asset_path || '').localeCompare(String(right.unity_asset_path || ''));
  }).map((prefab, index) => {
    const sourcePath = String(prefab.source_relative_path || '');
    const unityPath = String(prefab.unity_asset_path || '');
    const name = baseName(unityPath || sourcePath).replace(/\.prefab$/i, '') || `original_prefab_${index + 1}`;
    return {
      id: `original_prefab:${unityPath || sourcePath || index}`,
      name,
      kind: 'original_prefab',
      root_type: 'Prefab',
      asset_type: 'Prefab',
      source_file: sourcePath,
      path_id: '',
      preview_status: 'ready',
      reconstruction_status: 'original',
      unity_export_status: 'ready',
      unity_export_relative_path: `unity-export/${unityPath}`,
      unity_asset_path: unityPath,
      preview_relative_path: prefab.preview_relative_path || '',
      particle_system_count: prefab.particle_system_count || 0,
      source_relative_path: sourcePath,
      tags: ['vfx:prefab'],
      original_prefab: true,
    };
  });
}

function semanticRenderMode(item) {
  if (item?.original_prefab) return 'original_prefab';
  const tags = itemTags(item);
  if (tags.includes('vfx:fire')) return 'fire';
  if (tags.includes('vfx:smoke')) return 'smoke';
  if (tags.includes('vfx:spark')) return 'spark';
  if (tags.includes('vfx:magic')) return 'magic';
  if (tags.includes('vfx:slash')) return 'slash';
  if (item?.root_type === 'TrailRenderer' || item?.root_type === 'LineRenderer') return 'trail';
  return 'particle';
}

function preferredInitialVfxItem() {
  const hasSemanticTag = (item) => itemTags(item).some((tag) => tag !== 'vfx:particle');
  return (
    items.find((item) => item.original_prefab && item.preview_status === 'ready')
    || items.find((item) => item.root_type === 'Prefab' && item.preview_status === 'ready')
    || items.find((item) => item.preview_status === 'ready' && hasSemanticTag(item))
    || items.find((item) => item.preview_status === 'partial' && hasSemanticTag(item))
    || items.find((item) => item.preview_status === 'ready')
    || items[0]
  );
}

function filteredItems() {
  const needle = search.value.trim().toLowerCase();
  const type = typeFilter.value;
  const tag = tagFilter.value;
  const status = statusFilter.value;
  return items.filter((item) => {
    const tags = itemTags(item);
    const haystack = `${item.name || ''} ${item.root_type || ''} ${item.kind || ''} ${tags.join(' ')}`.toLowerCase();
    return (
      (!needle || haystack.includes(needle))
      && (!type || item.root_type === type)
      && (!tag || tags.includes(tag))
      && (!status || item.preview_status === status)
    );
  });
}

function itemLabel(item) {
  return item.name || item.id || item.path_id || 'unnamed';
}

function renderList() {
  const previousScroll = list.scrollTop;
  const visible = filteredItems();
  list.replaceChildren();
  for (const item of visible.slice(0, 1200)) {
    const button = document.createElement('button');
    button.className = `item${selected && selected.id === item.id ? ' active' : ''}`;
    button.type = 'button';
    button.dataset.itemId = item.id || '';
    button.innerHTML = `
      <span class="itemName"></span>
      <span class="itemMeta">
        <span>${item.root_type || item.asset_type || ''}</span>
        <span>${item.preview_status || ''}</span>
        <span>${item.unity_export_status || ''}</span>
        <span>${itemTags(item).map(tagLabel).join(', ')}</span>
      </span>
    `;
    button.querySelector('.itemName').textContent = itemLabel(item);
    button.addEventListener('click', () => selectItem(item));
    list.appendChild(button);
  }
  list.scrollTop = previousScroll;
}

function updateActiveItem() {
  for (const element of list.querySelectorAll('.item')) {
    element.classList.toggle('active', Boolean(selected && element.dataset.itemId === selected.id));
  }
}

function fillFilterOptions() {
  const rootTypes = [...new Set(items.map((item) => item.root_type || item.asset_type).filter(Boolean))].sort();
  const tags = [...new Set(items.flatMap((item) => itemTags(item)))].sort();
  for (const rootType of rootTypes) {
    const option = document.createElement('option');
    option.value = rootType;
    option.textContent = rootType;
    typeFilter.appendChild(option);
  }
  for (const tag of tags) {
    const option = document.createElement('option');
    option.value = tag;
    option.textContent = tagLabel(tag);
    tagFilter.appendChild(option);
  }
}

async function selectItem(item) {
  selected = item;
  particles = [];
  spawnCarry = 0;
  prefabSpawnCarry = [];
  selectedPayload = null;
  texture = null;
  title.textContent = itemLabel(item);
  subtitle.textContent = `${item.root_type || item.asset_type || ''} · ${item.source_file || ''} · path_id ${item.path_id || ''}`;
  statusLine.textContent = `preview=${item.preview_status || ''} reconstruction=${item.reconstruction_status || ''} unity_export=${item.unity_export_status || ''} render=${semanticRenderMode(item)}`;
  updateActiveItem();
  if (item.original_prefab) {
    metadata.textContent = 'loading prefab preview...';
    try {
      const preview = await api.readOriginalPrefabPreview(item.preview_relative_path);
      selectedPayload = {
        reconstruction: {
          ...preview,
          root_type: 'Prefab',
          original_prefab: true,
          unity_asset_path: item.unity_asset_path || preview.unity_asset_path || '',
        },
      };
      metadata.textContent = JSON.stringify(selectedPayload.reconstruction, null, 2);
      return;
    } catch (error) {
      selectedPayload = {
        reconstruction: {
          schema_version: 1,
          engine: 'unity',
          kind: 'original_prefab',
          root_type: 'Prefab',
          name: itemLabel(item),
          source_relative_path: item.source_relative_path || '',
          unity_asset_path: item.unity_asset_path || '',
          original_assets_root: 'Assets/GameAnalysisVfxPreview/OriginalAssets',
          particle_systems: [],
          status: 'failed',
        },
      };
      metadata.textContent = String(error && error.stack ? error.stack : error);
      return;
    }
  }
  metadata.textContent = 'loading...';
  try {
    const payload = await api.readReconstruction(item.reconstruction_relative_path);
    selectedPayload = payload;
    metadata.textContent = JSON.stringify(payload.reconstruction, null, 2);
    if (payload.textureUrl) {
      texture = new Image();
      texture.src = payload.textureUrl;
    }
  } catch (error) {
    metadata.textContent = String(error && error.stack ? error.stack : error);
  }
}

function spawnParticle(mode = 'particle') {
  const modules = currentModules();
  const main = modules.main || {};
  const shape = modules.shape || {};
  const lifetime = Math.max(0.25, constantValue(main.startLifetime, 1.2));
  const speed = Math.max(0.05, constantValue(main.startSpeed, 1.8));
  const size = Math.max(2, constantValue(main.startSize, 0.35) * 42);
  const angle = norm(shape.angle, 35) * Math.PI / 180;
  const radius = Math.max(4, norm(shape.radius, 0.35) * 120);
  let originX = canvas.width * 0.5 + (Math.random() - 0.5) * radius;
  let originY = canvas.height * 0.58 + (Math.random() - 0.5) * radius * 0.35;
  let theta = -Math.PI / 2 + (Math.random() - 0.5) * angle * 2;
  let velocity = speed * 95 * (0.55 + Math.random() * 0.75);
  let particleLife = lifetime;
  let particleSize = size * (0.65 + Math.random() * 0.8);
  let color = colorString(selectedPayload.reconstruction);
  let kind = 'orb';

  if (mode === 'fire') {
    originY = canvas.height * 0.72 + Math.random() * canvas.height * 0.08;
    theta = -Math.PI / 2 + (Math.random() - 0.5) * 0.72;
    velocity = speed * 70 * (0.55 + Math.random() * 0.9);
    particleLife = lifetime * (0.55 + Math.random() * 0.45);
    particleSize = size * (0.75 + Math.random() * 1.25);
    color = Math.random() > 0.42 ? 'rgba(255, 108, 32, 0.95)' : 'rgba(255, 220, 96, 0.92)';
  } else if (mode === 'smoke') {
    originY = canvas.height * 0.68 + Math.random() * canvas.height * 0.08;
    theta = -Math.PI / 2 + (Math.random() - 0.5) * 0.95;
    velocity = speed * 34 * (0.45 + Math.random() * 0.7);
    particleLife = lifetime * (1.8 + Math.random() * 1.4);
    particleSize = size * (1.5 + Math.random() * 2.2);
    color = 'rgba(155, 164, 171, 0.42)';
  } else if (mode === 'spark') {
    const burst = Math.random() * Math.PI * 2;
    originX = canvas.width * 0.5 + (Math.random() - 0.5) * radius * 0.35;
    originY = canvas.height * 0.56 + (Math.random() - 0.5) * radius * 0.25;
    theta = burst;
    velocity = speed * 180 * (0.7 + Math.random() * 1.5);
    particleLife = Math.max(0.18, lifetime * (0.25 + Math.random() * 0.25));
    particleSize = Math.max(3, size * (0.18 + Math.random() * 0.28));
    color = Math.random() > 0.35 ? 'rgba(255, 238, 156, 0.96)' : 'rgba(135, 221, 255, 0.92)';
    kind = 'streak';
  } else if (mode === 'magic') {
    const orbit = Math.random() * Math.PI * 2;
    const orbitRadius = canvas.height * (0.12 + Math.random() * 0.12);
    originX = canvas.width * 0.5 + Math.cos(orbit) * orbitRadius;
    originY = canvas.height * 0.5 + Math.sin(orbit) * orbitRadius * 0.62;
    theta = orbit + Math.PI / 2 + (Math.random() - 0.5) * 0.8;
    velocity = speed * 46 * (0.35 + Math.random() * 0.55);
    particleLife = lifetime * (0.9 + Math.random() * 0.9);
    particleSize = size * (0.38 + Math.random() * 0.52);
    color = Math.random() > 0.5 ? 'rgba(116, 231, 255, 0.88)' : 'rgba(177, 125, 255, 0.84)';
  }

  particles.push({
    x: originX,
    y: originY,
    vx: Math.cos(theta) * velocity,
    vy: Math.sin(theta) * velocity,
    age: 0,
    life: particleLife,
    size: particleSize,
    spin: Math.random() * Math.PI,
    color,
    kind,
  });
}

function updateParticles(dt, mode = 'particle') {
  const modules = currentModules();
  const emission = modules.emission || {};
  const main = modules.main || {};
  const rate = Math.min(300, Math.max(2, constantValue(emission.rateOverTime, 24)));
  const maxParticles = Math.min(1200, Math.max(64, norm(main.maxParticles, 256) || 256));
  spawnCarry += rate * dt;
  while (spawnCarry >= 1 && particles.length < maxParticles) {
    spawnParticle(mode);
    spawnCarry -= 1;
  }
  const gravity = mode === 'smoke'
    ? -12
    : constantValue(main.gravityModifier, mode === 'spark' ? 0.15 : 0) * 80;
  for (const p of particles) {
    p.age += dt;
    p.vy += gravity * dt;
    p.x += p.vx * dt;
    p.y += p.vy * dt;
    p.spin += dt;
  }
  particles = particles.filter((p) => p.age < p.life);
}

function drawParticleSprite(p, fallbackColor) {
  const alpha = Math.max(0, 1 - p.age / p.life);
  const size = p.size * (0.6 + alpha * 0.6);
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(p.x, p.y);
  ctx.rotate(p.spin);
  if (p.kind === 'streak') {
    ctx.strokeStyle = p.color || fallbackColor;
    ctx.lineWidth = Math.max(1.5, size * 0.22);
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(-size * 1.8, 0);
    ctx.lineTo(size * 0.85, 0);
    ctx.stroke();
  } else if (texture && texture.complete && texture.naturalWidth > 0) {
    ctx.drawImage(texture, -size / 2, -size / 2, size, size);
  } else {
    const gradient = ctx.createRadialGradient(0, 0, 0, 0, 0, size / 2);
    gradient.addColorStop(0, p.color || fallbackColor);
    gradient.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.arc(0, 0, size / 2, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

function drawParticleSystem(dt, mode = 'particle') {
  updateParticles(dt, mode);
  const color = colorString(selectedPayload.reconstruction);
  ctx.globalCompositeOperation = 'lighter';
  for (const p of particles) {
    drawParticleSprite(p, color);
  }
  ctx.globalCompositeOperation = 'source-over';
}

function drawFireEffect(dt, now) {
  drawParticleSystem(dt, 'fire');
  const flicker = 0.72 + Math.sin(now * 0.024) * 0.12 + Math.random() * 0.08;
  const glow = ctx.createRadialGradient(canvas.width * 0.5, canvas.height * 0.72, 0, canvas.width * 0.5, canvas.height * 0.72, canvas.height * 0.34);
  glow.addColorStop(0, `rgba(255, 118, 28, ${0.24 * flicker})`);
  glow.addColorStop(0.45, `rgba(255, 48, 16, ${0.12 * flicker})`);
  glow.addColorStop(1, 'rgba(255, 70, 20, 0)');
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
}

function drawSmokeEffect(dt, now) {
  drawParticleSystem(dt, 'smoke');
  ctx.save();
  ctx.globalAlpha = 0.16;
  ctx.strokeStyle = 'rgba(210, 220, 226, 0.35)';
  ctx.lineWidth = 2;
  for (let i = 0; i < 4; i += 1) {
    const y = canvas.height * (0.32 + i * 0.1);
    ctx.beginPath();
    for (let x = 0; x <= canvas.width; x += 28) {
      const wave = Math.sin(x * 0.018 + now * 0.0012 + i) * canvas.height * 0.018;
      if (x === 0) ctx.moveTo(x, y + wave);
      else ctx.lineTo(x, y + wave);
    }
    ctx.stroke();
  }
  ctx.restore();
}

function drawSparkEffect(dt, now) {
  if (particles.length < 10 || Math.random() > 0.78) spawnCarry += 12;
  drawParticleSystem(dt, 'spark');
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.strokeStyle = 'rgba(255, 250, 190, 0.55)';
  ctx.lineWidth = 1.5;
  const rays = 8;
  const pulse = 0.55 + Math.sin(now * 0.018) * 0.22;
  for (let i = 0; i < rays; i += 1) {
    const theta = (Math.PI * 2 * i) / rays + now * 0.001;
    const length = canvas.height * (0.08 + pulse * 0.05);
    ctx.beginPath();
    ctx.moveTo(canvas.width * 0.5, canvas.height * 0.55);
    ctx.lineTo(canvas.width * 0.5 + Math.cos(theta) * length, canvas.height * 0.55 + Math.sin(theta) * length);
    ctx.stroke();
  }
  ctx.restore();
}

function drawSlashEffect(now) {
  const modules = currentModules();
  const line = modules.line || {};
  const width = Math.max(3, constantValue(line.widthMultiplier, 1) * 9);
  const centerX = canvas.width * 0.5;
  const centerY = canvas.height * 0.52;
  const radius = Math.min(canvas.width, canvas.height) * 0.34;
  const sweep = (now * 0.004) % (Math.PI * 2);

  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  for (let layer = 0; layer < 3; layer += 1) {
    ctx.lineWidth = width * (1 + layer * 0.85);
    ctx.strokeStyle = layer === 0 ? 'rgba(255, 248, 230, 0.92)' : layer === 1 ? 'rgba(120, 220, 255, 0.42)' : 'rgba(106, 95, 255, 0.22)';
    ctx.shadowColor = ctx.strokeStyle;
    ctx.shadowBlur = 16 + layer * 14;
    ctx.beginPath();
    ctx.arc(centerX, centerY, radius + layer * 4, sweep - 1.15, sweep + 1.15);
    ctx.stroke();
  }
  ctx.globalAlpha = 0.4;
  ctx.fillStyle = 'rgba(255, 255, 255, 0.6)';
  ctx.beginPath();
  ctx.ellipse(centerX + Math.cos(sweep) * radius, centerY + Math.sin(sweep) * radius, width * 0.7, width * 0.28, sweep, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawMagicEffect(dt, now) {
  drawParticleSystem(dt, 'magic');
  const centerX = canvas.width * 0.5;
  const centerY = canvas.height * 0.5;
  const radius = Math.min(canvas.width, canvas.height) * 0.22;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.translate(centerX, centerY);
  ctx.rotate(now * 0.0008);
  for (let i = 0; i < 3; i += 1) {
    ctx.strokeStyle = i % 2 === 0 ? 'rgba(105, 232, 255, 0.56)' : 'rgba(183, 126, 255, 0.5)';
    ctx.lineWidth = 2 + i;
    ctx.shadowColor = ctx.strokeStyle;
    ctx.shadowBlur = 16;
    ctx.beginPath();
    ctx.ellipse(0, 0, radius * (1 + i * 0.16), radius * (0.36 + i * 0.08), (Math.PI * i) / 3, 0, Math.PI * 2);
    ctx.stroke();
  }
  for (let i = 0; i < 6; i += 1) {
    const theta = now * 0.0016 + (Math.PI * 2 * i) / 6;
    ctx.fillStyle = i % 2 === 0 ? 'rgba(143, 241, 255, 0.85)' : 'rgba(210, 155, 255, 0.78)';
    ctx.beginPath();
    ctx.arc(Math.cos(theta) * radius, Math.sin(theta) * radius * 0.42, 4, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

function prefabParticleSystems() {
  const systems = selectedPayload?.reconstruction?.particle_systems;
  return Array.isArray(systems) ? systems : [];
}

function deterministicHue(value) {
  let hash = 0;
  for (const ch of String(value || 'prefab')) {
    hash = ((hash << 5) - hash + ch.charCodeAt(0)) | 0;
  }
  return Math.abs(hash) % 360;
}

function prefabSystemColor(system, alpha = 0.72) {
  const guid = system?.renderer?.materialGuids?.[0] || system?.game_object_name || selected?.name;
  const hue = deterministicHue(guid);
  return `hsla(${hue}, 82%, 66%, ${alpha})`;
}

function spawnPrefabParticle(system, index, total) {
  const main = system.main || {};
  const emission = system.emission || {};
  const shape = system.shape || {};
  const complexitySize = total > 48 ? 0.28 : total > 16 ? 0.45 : 1;
  const colorAlpha = total > 48 ? 0.28 : total > 16 ? 0.46 : 0.78;
  const lifetime = Math.max(0.18, constantValue(main.startLifetime, 1.0));
  const speed = Math.max(0.02, constantValue(main.startSpeed, 0.8));
  const size = Math.max(1.5, constantValue(main.startSize, 0.35) * 34 * complexitySize);
  const lane = total <= 1 ? 0.5 : (index + 0.5) / total;
  const baseX = canvas.width * (0.22 + (lane % 4) * 0.18);
  const baseY = canvas.height * (0.36 + Math.floor(index / 4) * 0.11);
  const angle = norm(shape.angle, 25) * Math.PI / 180;
  const radius = Math.max(2, norm(shape.radius, 1) * 24);
  const theta = -Math.PI / 2 + (Math.random() - 0.5) * angle * 2;
  const velocity = speed * 70 * (0.35 + Math.random() * 0.85);
  particles.push({
    x: baseX + (Math.random() - 0.5) * radius,
    y: baseY + (Math.random() - 0.5) * radius,
    vx: Math.cos(theta) * velocity,
    vy: Math.sin(theta) * velocity,
    age: 0,
    life: lifetime,
    size: size * (0.55 + Math.random() * 0.9),
    spin: Math.random() * Math.PI,
    color: prefabSystemColor(system, colorAlpha),
    kind: 'orb',
  });
  if (particles.length > 1400) particles.shift();
}

function updatePrefabParticles(dt) {
  const systems = prefabParticleSystems().slice(0, 48);
  if (prefabSpawnCarry.length < systems.length) {
    prefabSpawnCarry = [...prefabSpawnCarry, ...Array(systems.length - prefabSpawnCarry.length).fill(0)];
  }
  systems.forEach((system, index) => {
    const emission = system.emission || {};
    if (emission.enabled === false) return;
    const complexityRate = systems.length > 48 ? 0.08 : systems.length > 16 ? 0.22 : 1;
    const rate = Math.min(36, Math.max(0.25, constantValue(emission.rateOverTime, 8) * complexityRate));
    prefabSpawnCarry[index] += rate * dt;
    while (prefabSpawnCarry[index] >= 1) {
      spawnPrefabParticle(system, index, systems.length);
      prefabSpawnCarry[index] -= 1;
    }
  });
  for (const p of particles) {
    p.age += dt;
    p.vy += 8 * dt;
    p.x += p.vx * dt;
    p.y += p.vy * dt;
    p.spin += dt;
  }
  particles = particles.filter((p) => p.age < p.life);
}

function drawOriginalPrefabParticlePreview(dt, now) {
  const systems = prefabParticleSystems();
  ctx.save();
  ctx.fillStyle = 'rgba(18, 22, 28, 0.92)';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (systems.length === 0) {
    ctx.fillStyle = '#d9f2ff';
    ctx.font = `${Math.max(16, Math.floor(canvas.width / 64))}px Segoe UI`;
    ctx.fillText('Original prefab preview data is unavailable.', 28, 42);
    ctx.restore();
    return;
  }
  updatePrefabParticles(dt);
  ctx.globalCompositeOperation = 'lighter';
  for (const p of particles) drawParticleSprite(p, p.color || 'rgba(160, 220, 255, 0.9)');
  ctx.globalCompositeOperation = 'source-over';
  ctx.fillStyle = '#d9f2ff';
  ctx.font = `${Math.max(14, Math.floor(canvas.width / 82))}px Segoe UI`;
  ctx.fillText(`${selectedPayload.reconstruction.name || selected?.name || 'Prefab'} · ${systems.length} ParticleSystem`, 24, 32);
  ctx.fillStyle = '#89a7bd';
  ctx.font = `${Math.max(11, Math.floor(canvas.width / 112))}px Segoe UI`;
  ctx.fillText(String(selected?.unity_asset_path || '').slice(0, 120), 24, 56);
  const markerCount = Math.min(systems.length, 48);
  for (let i = 0; i < markerCount; i += 1) {
    const lane = markerCount <= 1 ? 0.5 : (i + 0.5) / markerCount;
    const x = canvas.width * (0.22 + (lane % 4) * 0.18);
    const y = canvas.height * (0.36 + Math.floor(i / 4) * 0.11);
    ctx.fillStyle = prefabSystemColor(systems[i]);
    ctx.beginPath();
    ctx.arc(x, y, 3.5, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

function drawSemanticParticleSystem(dt, now) {
  const tags = itemTags(selected);
  const mode = tags.includes('vfx:fire')
    ? 'fire'
    : tags.includes('vfx:smoke')
      ? 'smoke'
      : tags.includes('vfx:spark')
        ? 'spark'
        : tags.includes('vfx:magic')
          ? 'magic'
          : tags.includes('vfx:slash')
            ? 'slash'
            : semanticRenderMode(selected);
  if (mode === 'fire') {
    drawFireEffect(dt, now);
  } else if (mode === 'smoke') {
    drawSmokeEffect(dt, now);
  } else if (mode === 'spark') {
    drawSparkEffect(dt, now);
  } else if (mode === 'magic') {
    drawMagicEffect(dt, now);
  } else if (mode === 'slash') {
    drawSlashEffect(now);
  } else {
    drawParticleSystem(dt, 'particle');
  }
}

function drawTrail(time) {
  const modules = currentModules();
  const renderer = modules.renderer || {};
  const line = modules.line || {};
  const width = Math.max(2, constantValue(line.widthMultiplier, 1) * 8);
  ctx.lineWidth = width;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.strokeStyle = colorString(selectedPayload.reconstruction);
  ctx.shadowColor = ctx.strokeStyle;
  ctx.shadowBlur = 18;
  ctx.beginPath();
  const points = 88;
  for (let i = 0; i < points; i += 1) {
    const t = i / (points - 1);
    const x = canvas.width * (0.12 + t * 0.76);
    const wave = Math.sin(t * Math.PI * 4 + time * 0.004) * canvas.height * 0.09;
    const y = canvas.height * 0.5 + wave + Math.cos(t * Math.PI * 2 + time * 0.002) * canvas.height * 0.08;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();
  ctx.shadowBlur = 0;
  if (renderer.textureRef) {
    ctx.fillStyle = '#9aa7b7';
    ctx.fillText(`texture: ${renderer.textureRef}`, 18, canvas.height - 22);
  }
}

function drawFrame(now) {
  const dt = Math.min(0.05, (now - lastTime) / 1000);
  lastTime = now;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = 'rgba(10, 12, 15, 0.28)';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.font = `${Math.max(12, Math.floor(canvas.width / 90))}px Segoe UI`;
  if (selectedPayload?.reconstruction) {
    const rootType = selectedPayload.reconstruction.root_type;
    const mode = semanticRenderMode(selected);
    if (mode === 'original_prefab') {
      drawOriginalPrefabParticlePreview(dt, now);
    } else if (mode === 'slash') {
      drawSlashEffect(now);
    } else if (rootType === 'TrailRenderer' || rootType === 'LineRenderer') {
      drawTrail(now);
    } else {
      drawSemanticParticleSystem(dt, now);
    }
  }
  requestAnimationFrame(drawFrame);
}

async function init() {
  resizeCanvas();
  const data = await api.getInitialData();
  items = [...originalPrefabItems(data.manifest), ...(data.items || [])];
  const ready = items.filter((item) => item.preview_status === 'ready').length;
  const partial = items.filter((item) => item.preview_status === 'partial').length;
  const rootSummary = [...new Set(items.map((item) => item.root_type || item.asset_type).filter(Boolean))].join(' / ');
  const tagSummary = [...new Set(items.flatMap((item) => itemTags(item)).map(tagLabel))].slice(0, 8).join(' / ');
  summary.textContent = `VFX ${items.length} · ready ${ready} · partial ${partial} · ${rootSummary}${tagSummary ? ` · ${tagSummary}` : ''}`;
  fillFilterOptions();
  search.addEventListener('input', renderList);
  typeFilter.addEventListener('change', renderList);
  tagFilter.addEventListener('change', renderList);
  statusFilter.addEventListener('change', renderList);
  document.getElementById('openUnityExport').addEventListener('click', () => api.openUnityExport());
  renderList();
  const first = preferredInitialVfxItem();
  if (first) await selectItem(first);
  requestAnimationFrame(drawFrame);
}

window.addEventListener('resize', resizeCanvas);
init().catch((error) => {
  summary.textContent = '加载失败';
  title.textContent = 'GameVfxViewer 加载失败';
  statusLine.textContent = '无法读取 manifest 或 reconstructed 数据';
  metadata.textContent = String(error && error.stack ? error.stack : error);
});
