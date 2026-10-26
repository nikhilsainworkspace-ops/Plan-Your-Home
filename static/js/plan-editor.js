(() => {
  'use strict';
  const root = document.getElementById('plan-editor');
  const svg = document.getElementById('plan-canvas');
  if (!root || !svg) return;

  const canWrite = root.dataset.canWrite === 'true';
  const csrf = root.dataset.csrf || '';
  const saveUrl = root.dataset.saveUrl || '';
  const plotWidth = Number(root.dataset.plotWidth || 0);
  const plotLength = Number(root.dataset.plotLength || 0);
  const status = document.getElementById('save-status');
  const saveButton = document.getElementById('save-plan');
  const undoButton = document.getElementById('undo-move');
  const redoButton = document.getElementById('redo-move');
  let selected = null;
  let drag = null;
  let dirty = false;
  let savedSnapshot = [];
  const undoStack = [];
  const redoStack = [];
  const roomGroups = [...svg.querySelectorAll('[data-room-id]')];

  const roundQuarter = (v) => Math.round(v * 4) / 4;
  const clamp = (n, lo, hi) => Math.min(Math.max(n, lo), hi);
  const roomState = (g) => ({
    id: Number(g.dataset.roomId), x_m: Number(g.dataset.x), y_m: Number(g.dataset.y),
    width_m: Number(g.dataset.width), depth_m: Number(g.dataset.depth),
  });
  const snapshot = () => roomGroups.map(roomState);
  const copyState = (state) => state.map((x) => ({...x}));

  function setStatus(text, cls = '') {
    if (!status) return;
    status.textContent = text;
    status.classList.remove('status-unsaved', 'status-saving', 'status-save-error');
    if (cls) status.classList.add(cls);
  }
  function markDirty() {
    dirty = true;
    setStatus('Unsaved changes', 'status-unsaved');
    if (saveButton) saveButton.disabled = !canWrite;
  }
  function updateShape(g) {
    const x = Number(g.dataset.x), y = Number(g.dataset.y), w = Number(g.dataset.width), d = Number(g.dataset.depth);
    const rect = g.querySelector('rect.room-rect');
    const texts = g.querySelectorAll('text');
    const door = g.querySelector('path.room-door-mark');
    if (rect) { rect.setAttribute('x', x); rect.setAttribute('y', y); rect.setAttribute('width', w); rect.setAttribute('height', d); }
    if (texts[0]) { texts[0].setAttribute('x', x + w / 2); texts[0].setAttribute('y', y + d / 2 - 0.12); }
    if (texts[1]) { texts[1].setAttribute('x', x + w / 2); texts[1].setAttribute('y', y + d / 2 + 0.28); texts[1].textContent = `${w.toFixed(2).replace(/\.00$/, '')} × ${d.toFixed(2).replace(/\.00$/, '')} m`; }
    if (door) door.setAttribute('d', `M ${x + w / 2 - 0.3} ${y + d} h 0.6`);
    const mini = document.querySelector(`[data-select-room="${g.dataset.roomId}"] small`);
    if (mini) mini.textContent = `${(w*d).toFixed(2).replace(/\.00$/, '')} m² · floor ${g.dataset.floor || '1'}`;
  }
  function applyState(state) {
    const map = new Map(state.map((r) => [Number(r.id), r]));
    roomGroups.forEach((g) => {
      const room = map.get(Number(g.dataset.roomId));
      if (!room) return;
      g.dataset.x = room.x_m; g.dataset.y = room.y_m; g.dataset.width = room.width_m; g.dataset.depth = room.depth_m;
      updateShape(g);
    });
    markDirty();
  }
  function remember() {
    undoStack.push(copyState(snapshot()));
    if (undoStack.length > 50) undoStack.shift();
    redoStack.length = 0;
    refreshHistoryButtons();
  }
  function refreshHistoryButtons() {
    if (undoButton) undoButton.disabled = !canWrite || undoStack.length === 0;
    if (redoButton) redoButton.disabled = !canWrite || redoStack.length === 0;
  }
  function choose(g) {
    selected?.classList.remove('room-selected');
    selected = g;
    selected?.classList.add('room-selected');
    document.querySelectorAll('[data-select-room]').forEach((b) => b.classList.toggle('room-mini-selected', b.dataset.selectRoom === g?.dataset.roomId));
  }
  function pointForEvent(event) {
    const point = svg.createSVGPoint();
    point.x = event.clientX; point.y = event.clientY;
    const matrix = svg.getScreenCTM();
    if (!matrix) return {x: 0, y: 0};
    const transformed = point.matrixTransform(matrix.inverse());
    return {x: transformed.x, y: transformed.y};
  }
  function moveRoom(g, x, y) {
    const w = Number(g.dataset.width), d = Number(g.dataset.depth);
    g.dataset.x = clamp(roundQuarter(x), 0, Math.max(0, plotWidth - w));
    g.dataset.y = clamp(roundQuarter(y), 0, Math.max(0, plotLength - d));
    updateShape(g);
  }

  roomGroups.forEach((g) => {
    g.addEventListener('pointerdown', (event) => {
      choose(g);
      if (!canWrite || event.button !== 0) return;
      const point = pointForEvent(event);
      drag = {g, start: point, before: roomState(g), moved: false};
      g.setPointerCapture?.(event.pointerId);
    });
    g.addEventListener('pointermove', (event) => {
      if (!drag || drag.g !== g || !canWrite) return;
      const point = pointForEvent(event);
      const dx = point.x - drag.start.x, dy = point.y - drag.start.y;
      if (Math.abs(dx) + Math.abs(dy) > 0.04) drag.moved = true;
      if (drag.moved) moveRoom(g, drag.before.x_m + dx, drag.before.y_m + dy);
    });
    const endDrag = () => {
      if (!drag || drag.g !== g) return;
      if (drag.moved) { remember(); markDirty(); }
      drag = null;
    };
    g.addEventListener('pointerup', endDrag);
    g.addEventListener('pointercancel', endDrag);
    g.addEventListener('keydown', (event) => {
      if (!canWrite || !['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      choose(g); remember();
      const delta = event.shiftKey ? 1 : 0.25;
      const room = roomState(g);
      if (event.key === 'ArrowUp') moveRoom(g, room.x_m, room.y_m - delta);
      if (event.key === 'ArrowDown') moveRoom(g, room.x_m, room.y_m + delta);
      if (event.key === 'ArrowLeft') moveRoom(g, room.x_m - delta, room.y_m);
      if (event.key === 'ArrowRight') moveRoom(g, room.x_m + delta, room.y_m);
      markDirty();
    });
    g.addEventListener('click', () => choose(g));
  });

  document.querySelectorAll('[data-select-room]').forEach((button) => {
    button.addEventListener('click', () => {
      const g = roomGroups.find((item) => item.dataset.roomId === button.dataset.selectRoom);
      if (g) { choose(g); g.focus({preventScroll: true}); }
    });
  });
  document.querySelectorAll('[data-focus-room]').forEach((button) => {
    button.addEventListener('click', () => {
      const g = roomGroups.find((item) => item.dataset.roomId === button.dataset.focusRoom);
      if (g) { choose(g); g.focus({preventScroll: true}); }
    });
  });

  undoButton?.addEventListener('click', () => {
    if (!undoStack.length) return;
    redoStack.push(copyState(snapshot()));
    applyState(undoStack.pop()); refreshHistoryButtons();
  });
  redoButton?.addEventListener('click', () => {
    if (!redoStack.length) return;
    undoStack.push(copyState(snapshot()));
    applyState(redoStack.pop()); refreshHistoryButtons();
  });

  let zoom = 100;
  function applyZoom() {
    svg.classList.remove('zoom-75','zoom-100','zoom-125','zoom-150');
    svg.classList.add(`zoom-${zoom}`);
  }
  document.getElementById('zoom-in')?.addEventListener('click', () => { zoom = Math.min(150, zoom + 25); applyZoom(); });
  document.getElementById('zoom-out')?.addEventListener('click', () => { zoom = Math.max(75, zoom - 25); applyZoom(); });

  saveButton?.addEventListener('click', async () => {
    if (!canWrite || !dirty) return;
    saveButton.disabled = true;
    setStatus('Saving…', 'status-saving');
    try {
      const response = await fetch(saveUrl, {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf, 'Accept': 'application/json'},
        body: JSON.stringify({rooms: snapshot()}),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || 'Save failed. Please try again.');
      dirty = false; savedSnapshot = copyState(snapshot());
      setStatus(`Saved ${result.updated} room${result.updated === 1 ? '' : 's'} at ${new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}`);
      undoStack.length = 0; redoStack.length = 0; refreshHistoryButtons();
    } catch (error) {
      setStatus(error.message || 'Could not save the plan.', 'status-save-error');
    } finally {
      saveButton.disabled = !canWrite || !dirty;
    }
  });

  savedSnapshot = copyState(snapshot());
  refreshHistoryButtons();
  window.addEventListener('beforeunload', (event) => {
    if (dirty && canWrite) { event.preventDefault(); event.returnValue = ''; }
  });
})();
