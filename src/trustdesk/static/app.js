const q = document.getElementById('q');
const key = document.getElementById('k');
const out = document.getElementById('out');

q.addEventListener('keydown', async (e) => {
  if (e.key !== 'Enter' || !q.value.trim()) return;
  out.textContent = 'Searching…';
  const headers = key.value ? { Authorization: 'Bearer ' + key.value } : {};
  let r;
  try {
    r = await fetch('/api/search?q=' + encodeURIComponent(q.value), { headers });
  } catch {
    out.textContent = 'Network error';
    return;
  }
  if (r.status === 401) { out.textContent = 'Unauthorized: enter a valid API key.'; return; }
  if (r.status === 429) { out.textContent = 'Too many failed attempts; wait a minute.'; return; }
  if (!r.ok) { out.textContent = 'Error ' + r.status; return; }
  const d = await r.json();
  out.replaceChildren();
  if (!d.results.length) { out.textContent = 'No results.'; return; }
  for (const x of d.results) {
    const el = document.createElement('div');
    el.className = 'r';
    const t = document.createElement('b');
    t.textContent = x.title + ' › ' + x.heading;
    const s = document.createElement('small');
    s.textContent = ' v' + x.version + ' · ' + x.source;
    const p = document.createElement('p');
    p.textContent = x.body;
    el.append(t, s, p);
    out.append(el);
  }
});
