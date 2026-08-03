/* Tribunal console front end.
   Every state on screen comes from a server event describing work that actually
   happened. Nothing here animates on a timer pretending to be progress - if the
   panel is quiet, the screen is quiet. */

const ADVOCATES = [
  { id: 'impact',   name: 'Impact',   model: 'ministral-8b',    stance: 'WHO BREAKS?',     c: '#c96a63', x: 13.5, y: 79 },
  { id: 'evidence', name: 'Evidence', model: 'magistral-small', stance: 'THE METADATA',    c: '#7fbf9b', x: 37.8, y: 79 },
  { id: 'minimal',  name: 'Minimal',  model: 'mistral-small',   stance: 'SMALLEST CHANGE', c: '#8fa8ff', x: 62.2, y: 79 },
  { id: 'cost',     name: 'Cost',     model: 'ministral-14b',   stance: 'COST OF ERROR',   c: '#c9a227', x: 86.5, y: 79 },
];

const $ = id => document.getElementById(id);
const esc = t => String(t == null ? '' : t).replace(/[&<>]/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[m]));
const idx = id => ADVOCATES.findIndex(a => a.id === id);
let chosenUrn = '', stream = null;

/* ---------- stage ---------- */
function buildStage() {
  const st = $('stage'), beams = $('beams');
  if (st.dataset.built) return;
  st.dataset.built = '1';
  ADVOCATES.forEach((a, i) => {
    const d = document.createElement('div');
    d.className = 'adv';
    d.id = 'adv' + i;
    d.style.cssText = `left:${a.x}%;top:${a.y}%;--lc:${a.c}`;
    d.innerHTML =
      `<div class="adv-h"><span class="dot"></span>
         <div><div class="adv-n">${a.name}</div><div class="adv-m">${a.model}</div></div></div>
       <span class="stance">${a.stance}</span><span class="score" id="sc${i}" hidden></span>
       <div class="say" id="say${i}">—</div><div class="defect" id="df${i}" hidden></div>`;
    st.appendChild(d);
    const x0 = a.x * 10, y0 = a.y * 5.2;
    const path = `M ${x0} ${y0} C ${x0} ${y0 - 70}, 500 ${y0 - 40}, 500 268`;
    beams.insertAdjacentHTML('beforeend',
      `<path class="beam" id="bm${i}" stroke="${a.c}" d="${path}"/>`);
  });
}

function resetStage() {
  ADVOCATES.forEach((_, i) => {
    $('adv' + i).className = 'adv';
    $('say' + i).innerHTML = '<span class="dots"><i></i><i></i><i></i></span>';
    $('sc' + i).hidden = true;
    $('df' + i).hidden = true;
    $('bm' + i).setAttribute('class', 'beam');
  });
  $('judge').classList.remove('ruled');
  $('jstate').textContent = 'listening';
  $('agree').hidden = true;
  $('ruling').hidden = true;
  $('ruling').innerHTML = '';
  ['r0', 'r1', 'r2'].forEach(r => $(r).className = 'chip');
  $('c-calls').textContent = '0';
  $('c-tok').textContent = '0';
  $('c-phase').textContent = 'starting';
}

/* ---------- asset search ---------- */
let sTimer;
$('asset').addEventListener('input', () => {
  clearTimeout(sTimer);
  chosenUrn = '';
  sTimer = setTimeout(async () => {
    const q = $('asset').value.trim();
    if (!q) { $('hits').hidden = true; return; }
    let d;
    try { d = await fetch('/api/search?q=' + encodeURIComponent(q)).then(r => r.json()); }
    catch { return; }
    const rows = d.assets || [];
    if (!rows.length) { $('hits').hidden = true; return; }
    $('hits').innerHTML = rows.map(r =>
      `<div class="hit" data-urn="${esc(r.urn)}">${esc(r.name || r.urn)}<small>${esc(r.urn)}</small></div>`).join('');
    $('hits').hidden = false;
    $('hits').querySelectorAll('.hit').forEach(el => el.onclick = () => {
      chosenUrn = el.dataset.urn;
      $('asset').value = el.querySelector('small').textContent;
      $('hits').hidden = true;
    });
  }, 220);
});
document.addEventListener('click', e => {
  if (!e.target.closest('.ask-row')) $('hits').hidden = true;
});

/* ---------- run ---------- */
$('go').onclick = () => {
  const question = $('question').value.trim();
  if (!question) { $('question').focus(); return; }
  // A urn typed straight into the box is honoured; otherwise use the picked one.
  const typed = $('asset').value.trim();
  const urn = chosenUrn || (typed.startsWith('urn:li:') ? typed : '');

  buildStage();
  $('brief').hidden = true;
  $('rail').hidden = false;
  $('stage').hidden = false;
  resetStage();
  $('go').disabled = true;
  $('go').textContent = 'deliberating…';
  if (stream) stream.close();

  const qs = new URLSearchParams({
    question, urn, write: $('write').checked ? '1' : '0',
  });
  stream = new EventSource('/api/ask?' + qs.toString());
  stream.onmessage = ev => {
    let e; try { e = JSON.parse(ev.data); } catch { return; }
    handle(e);
  };
  stream.onerror = () => { /* 'closed' ends it; a mid-flight drop stops the counters */ };
};

/* A score is a judgement handed down, so it lands rather than appears. Short,
   eased, and it always finishes on the EXACT value the judge gave - never a
   rounded approximation left behind by the animation. */
function countTo(el, target) {
  // Write the real value FIRST. requestAnimationFrame does not fire in a
  // backgrounded tab, so an animation that also owns the text left every score
  // blank - observed. The number is data; the count-up is decoration on top of
  // a value that is already correct and already on screen.
  el.textContent = target + '/10';
  if (!Number.isFinite(target) || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  const dur = 460, t0 = performance.now();
  const step = now => {
    const k = Math.min(1, (now - t0) / dur);
    const eased = 1 - Math.pow(1 - k, 3);
    el.textContent = (target * eased).toFixed(1) + '/10';
    if (k < 1) requestAnimationFrame(step);
    else el.textContent = target + '/10';
  };
  requestAnimationFrame(step);
}

function stop() {
  if (stream) { stream.close(); stream = null; }
  $('go').disabled = false;
  $('go').textContent = 'Convene the tribunal';
}

function handle(e) {
  if (typeof e.calls === 'number') $('c-calls').textContent = e.calls;
  if (typeof e.tokens === 'number') $('c-tok').textContent = e.tokens.toLocaleString();

  switch (e.kind) {
    case 'demo_notice':
      $('notice').hidden = false;
      $('notice').textContent = e.text;
      break;

    case 'prior': {
      // Refusing to re-argue is a FEATURE, so it gets the same prominence as a
      // ruling rather than being buried as a warning.
      $('stage').hidden = true;
      $('rail').hidden = true;
      const r = $('ruling');
      r.hidden = false;
      r.innerHTML = `<div class="prior"><h3>Already decided</h3>
        <p>DataHub already holds a ruling on this. Re-arguing it would produce a
        confident second opinion nobody asked for.</p><ul>` +
        (e.decisions || []).map(d => `<li>${esc(d.title)}</li>`).join('') + `</ul></div>`;
      stop();
      break;
    }

    case 'briefing':
      $('brief').hidden = false;
      $('brief-body').textContent = e.text;
      break;

    case 'round':
      if (e.round === 0) { $('r0').className = 'chip on'; $('c-phase').textContent = 'arguing in isolation'; ADVOCATES.forEach((_, i) => $('adv' + i).classList.add('active')); }
      if (e.round === 1) { $('r0').className = 'chip done'; $('r1').className = 'chip on'; $('c-phase').textContent = 'judge scoring'; $('jstate').textContent = 'reviewing'; }
      if (e.round === 2) { $('r1').className = 'chip done'; $('r2').className = 'chip on'; $('c-phase').textContent = 'refining'; (e.revising || []).forEach(id => { const i = idx(id); if (i >= 0) $('adv' + i).classList.add('active'); }); }
      break;

    case 'answer': case 'revision': {
      const i = idx(e.id); if (i < 0) break;
      $('say' + i).textContent = e.ok ? e.text : ('failed: ' + (e.error || ''));
      $('adv' + i).classList.remove('active');
      if (e.ok) { $('adv' + i).classList.add('done'); $('bm' + i).setAttribute('class', 'beam hot'); }
      break;
    }

    case 'scores':
      Object.entries(e.scores || {}).forEach(([id, s]) => {
        const i = idx(id); if (i < 0) return;
        const el = $('sc' + i);
        el.hidden = false;
        el.className = 'score ' + (s >= 8 ? 'hi' : s >= 6 ? 'mid' : 'lo');
        countTo(el, Number(s));
        const d = (e.defects || {})[id];
        if (d) { $('df' + i).hidden = false; $('df' + i).textContent = 'judge: ' + d; }
      });
      $('agree').hidden = false;
      $('agree-v').textContent = e.agreement || '—';
      $('agree-v').style.color = e.agreement === 'HIGH' ? 'var(--ok)' : 'var(--warn)';
      $('jstate').textContent = `best ${e.best || '?'} · spread ${e.spread}`;
      break;

    case 'critique':
      ADVOCATES.forEach((_, i) => $('bm' + i).setAttribute('class', 'beam back hot'));
      $('jstate').textContent = 'not good enough — critiquing';
      break;

    case 'verdict':
      $('judge').classList.add('ruled');
      $('jstate').textContent = e.case === 'ADOPT' ? 'adopted + improved' : 'ruled';
      renderRuling(e);
      break;

    case 'writing':
      appendWrite('writing the ruling back to DataHub…', false);
      break;

    case 'written':
      appendWrite(e.ok
        ? 'Saved as a DataHub <b>Decision</b> document linked to the asset, description stamped, tag applied. The next person or agent inherits the reasoning.'
          + (e.document_urn ? ` <code>${esc(e.document_urn)}</code>` : '')
        : 'Partial write-back: ' + esc((e.errors || []).join('; ')), !e.ok);
      break;

    case 'done':
      ['r0', 'r1', 'r2'].forEach(r => $(r).className = 'chip done');
      $('c-phase').textContent = 'complete';
      stop();
      break;

    case 'error':
      $('ruling').hidden = false;
      $('ruling').innerHTML = `<div class="prior"><h3>Could not deliberate</h3><p>${esc(e.error)}</p></div>`;
      stop();
      break;

    case 'closed':
      stop();
      break;
  }
}

function renderRuling(e) {
  const r = $('ruling');
  r.hidden = false;
  const label = e.case === 'ADOPT' ? 'adopted one advocate and improved it'
    : e.case === 'SETTLED' ? 'settled without debate' : 'synthesised after revisions';
  r.innerHTML =
    `<div class="r-top">
       <span class="r-title">⚖ Ruling</span>
       <span class="pills">
         <span class="pill">${esc(label)}</span>
         <span class="pill ${e.agreement === 'HIGH' ? 'good' : 'warn'}">agreement <b>${esc(e.agreement)}</b></span>
         <span class="pill">calls <b>${$('c-calls').textContent}</b></span>
         <span class="pill">tokens <b>${$('c-tok').textContent}</b></span>
       </span>
     </div>
     <div class="r-body">${esc(e.ruling)}</div>` +
    // The dissent is shown, never folded away. A ruling with the objection stripped
    // out looks more authoritative and is far less useful to whoever reads it next.
    (e.dissent ? `<div class="r-dissent"><b>Surviving objection:</b> ${esc(e.dissent)}</div>` : '') +
    `<div id="wrote"></div>`;
}

function appendWrite(html, isErr) {
  const w = $('wrote');
  if (w) w.innerHTML = `<div class="r-write${isErr ? ' err' : ''}">${html}</div>`;
}

/* ---------- boot ---------- */
(async () => {
  let h;
  try { h = await fetch('/api/health').then(r => r.json()); } catch { return; }
  const m = $('mode');
  if (h.demo) {
    m.textContent = 'DEMO — replaying a recording';
    m.className = 'mode demo';
  } else {
    const ok = h.datahub && h.datahub.ok;
    m.textContent = ok ? `LIVE — DataHub connected · ${h.keys} model keys`
                       : 'DataHub NOT reachable';
    m.className = 'mode ' + (ok ? 'live' : 'demo');
    if (!ok && h.datahub) {
      $('notice').hidden = false;
      $('notice').textContent = 'DataHub unreachable: ' + (h.datahub.error || '');
    }
  }
})();
