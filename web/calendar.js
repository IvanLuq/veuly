'use strict';
// The calendars (transcapp/calendar_ics.py): Settings › Calendars lists the linked calendars, each with its name, its side
// (School or Work) and Remove, and adds one from its secret iCal address (the server stores it in the system's key store
// and never sends it back: the page only learns the site's name). Each side's home page shows what is on now and next
// today in that side's calendars, with Start, which makes the class in its course and opens it ready to record. A
// meeting takes the event's title and its attendees. On the work side an event of no project, with attendees, is
// offered too, with a choice of project. Loaded after app.js: HOME_TILES, SETTINGS_PARTS.

ICONS.calendar = '<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 9.5h17M8 3v4M16 3v4M7.5 13h2M11 13h2M14.5 13h2M7.5 16.5h2M11 16.5h2"/>';

// replaceChildren writes null as text: what is not there is left out first
const calFill = (el, ...kids) => el.replaceChildren(...kids.flat().filter((k) => k != null && k !== false));
const calClock = (iso) => new Date(iso).toLocaleTimeString(getLang(), { hour: 'numeric', minute: '2-digit' });

// ---------------------------------------------------------------- the home page's tile
let calTimer = null;
// The tile changes with the clock (a class ends, the next one starts): the home page asks again at the next of those
// moments, or soon when a calendar was still being read.
function calWatch(cal) {
  clearTimeout(calTimer);
  const now = Date.now();
  const moments = [...(cal.now || []).map((e) => new Date(e.end).getTime()),
    ...(cal.next || []).map((e) => new Date(e.start).getTime() - 10 * 60000)].filter((ms) => ms > now);
  const wait = cal.loading ? 3000 : moments.length ? Math.min(...moments) - now + 1000 : 15 * 60000;
  calTimer = setTimeout(() => { if (S.view.name === 'home') refreshHome(); }, Math.max(3000, Math.min(wait, 15 * 60000)));
}

async function calStart(e, ev, slug) {
  const btn = e.currentTarget;
  if (prefsDirty) return toast(t('save_first'));
  if (!slug) return toast(t('cal_pick_ph'));
  btn.disabled = true;
  try {
    const date = new Date().toLocaleDateString(getLang(), { day: 'numeric', month: 'short', year: 'numeric' });
    const meta = await api('POST', '/api/calendar/start', { slug, uid: ev.uid, start: ev.start, cal: ev.cal || '', title: t('class_of', { date }) });
    await loadState();
    await go({ name: 'class', slug, cid: meta.id });
    const record = $('#record');
    if (record && S.view.cid === meta.id) record.focus();
  } catch (err) {
    btn.disabled = false;
    fail(err);
  }
}

// `from`: the calendar's name, when the side has more than one
function calRow(ev, when, i, from) {
  const id = `cal-${when}-${i}`;
  const time = `${calClock(ev.start)}–${calClock(ev.end)}`;
  const projects = !ev.slug ? shownCourses() : [];
  const pick = !ev.slug ? h('select', { id: `${id}-pick`, 'aria-label': t('cal_pick') },
    h('option', { value: '' }, t('cal_pick_ph')), projects.map((c) => h('option', { value: c.slug, selected: projects.length === 1 }, c.name))) : null;
  const action = ev.cid
    ? h('button', { class: 'btn', id: `${id}-open`, 'aria-describedby': `${id}-title`, onclick: () => go({ name: 'class', slug: ev.slug, cid: ev.cid }).catch(fail) }, t('cal_open'))
    : h('button', { class: `btn${when === 'now' ? ' main' : ''}`, id: `${id}-start`, 'aria-describedby': `${id}-title`,
      onclick: (e) => calStart(e, ev, ev.slug || (pick && pick.value)) }, t('cal_start'));
  const with_ = mode() === 'project' && (ev.attendees || []).length ? t('cal_with', { names: ev.attendees.join(', ') }) : '';
  return h('li', { class: `cal-row ${when}` },
    h('div', { class: 'cal-text' },
      h('span', { class: 'cal-when' }, `${t(when === 'now' ? 'cal_now' : 'cal_next')} · ${time}`, from ? h('span', { class: 'cal-from' }, ` · ${from}`) : null),
      h('span', { class: 'cal-title', id: `${id}-title` }, ev.slug ? ev.course : ev.title),
      ev.slug && ev.title && ev.title !== ev.course ? h('span', { class: 'cal-event' }, ev.title) : null,
      with_ ? h('span', { class: 'cal-event' }, with_) : null),
    h('span', { class: 'cal-side' }, pick, action));
}

function homeCalendar(home) {
  const cal = home.calendar;
  if (!cal || !cal.on) return null;
  calWatch(cal);
  const names = new Map((cal.calendars || []).map((c) => [c.id, c.name]));
  const from = (ev) => (names.size > 1 ? names.get(ev.cal) : '');
  const rows = [...(cal.now || []).map((ev, i) => calRow(ev, 'now', i, from(ev))), ...(cal.next || []).map((ev, i) => calRow(ev, 'next', i, from(ev)))];
  // one line for each calendar that could not be read: the others' events still show
  const troubles = (cal.troubles || []).map((x) => h('p', { class: 'meta warn cal-trouble', id: `cal-trouble-${x.id}` },
    `${t('cal_failed_one', { name: x.name })} ${HELP(x.kind)}`));
  if (!rows.length && !troubles.length && !cal.loading) return null;
  return h('section', { class: 'tile wide', id: 'home-calendar', 'aria-labelledby': 'home-calendar-h' },
    h('h3', { id: 'home-calendar-h', class: 'with-ico' }, icon('calendar'), t('cal_tile')),
    rows.length ? h('ul', { class: 'cal-list' }, rows) : cal.loading ? h('p', { class: 'soft' }, t('cal_loading')) : null,
    troubles.length ? h('div', { id: 'cal-trouble' }, troubles) : null);
}
HOME_TILES.unshift(homeCalendar);          // first: what is on now is what the home page is for at that moment

// ---------------------------------------------------------------- Settings › Calendars
let calLinking = false;
function calendarSettings() {
  const box = h('div', { class: 'sheet', id: 'calendar-settings' });
  const sides = (current) => ['course', 'project'].map((s) => h('option', { value: s, selected: s === current }, t(`mode_${s}`)));
  // Drawn again after a change, the keyboard stays where it was, and an address being typed in the form stays there.
  const redraw = (state, then) => {
    const el = document.activeElement;
    const id = el && box.contains(el) ? el.id : '';
    const typed = $('#cal-new') ? { url: $('#cal-url').value, side: $('#cal-new-side').value, name: $('#cal-new-name').value } : null;
    draw(state, !!typed, typed);
    const back = (id && document.getElementById(id)) || (then && then());
    if (back) back.focus({ preventScroll: true });
  };
  const change = async (cal, changes) => {
    try {
      redraw(await api('PUT', `/api/calendars/${cal.id}`, changes), () => $(`#cal-${cal.id}-side`));
      toast(t('saved'));
    } catch (err) { fail(err); }
  };
  const remove = (cal) => confirmPop({ id: 'cal-remove-pop', title: t('cal_remove_title', { name: cal.name }), body: t('cal_remove_body'),
    yes: t('cal_remove'), run: async () => {
      try {
        redraw(await api('DELETE', `/api/calendars/${cal.id}`), () => $('#calendar-settings [id$="-remove"]') || $('#cal-add') || $('#cal-url'));
        toast(t('cal_removed', { name: cal.name }));
      } catch (err) { fail(err); }
    } });
  const item = (cal) => {
    const at = cal.checked ? calClock(cal.checked) : '';
    const id = `cal-${cal.id}`;
    return h('li', { class: 'cal-item', id: `${id}-item` },
      h('div', { class: 'cal-item-row' },
        h('div', { class: 'cal-item-field' },
          h('label', { class: 'field', for: `${id}-name` }, t('cal_item_name')),
          h('input', { type: 'text', id: `${id}-name`, value: cal.name, maxlength: 60, autocomplete: 'off',
            onchange: (e) => change(cal, { name: e.target.value }) })),
        h('div', { class: 'cal-item-field' },
          h('label', { class: 'field', for: `${id}-side` }, t('cal_side')),
          h('select', { id: `${id}-side`, onchange: (e) => change(cal, { side: e.target.value }) }, sides(cal.side))),
        h('button', { class: 'btn quiet', id: `${id}-remove`, 'aria-label': t('cal_remove_one', { name: cal.name }), onclick: () => remove(cal) }, t('cal_remove'))),
      h('p', { class: 'soft cal-where-line' }, t('cal_on', { where: cal.where }), at ? ` ${t('cal_checked', { time: at })}` : ''),
      cal.moved ? h('div', { class: 'notice row gap', id: `${id}-moved` },        // why the link of before is on its side, until seen
        h('span', {}, t(`cal_moved_${cal.moved}`, { side: t(`mode_${cal.side}`) })),
        h('button', { class: 'btn quiet', id: `${id}-seen`, onclick: () => change(cal, {}) }, t('cal_moved_ok'))) : null,
      cal.error_kind ? h('p', { class: 'meta warn', id: `${id}-error` }, HELP(cal.error_kind)) : null);
  };
  const draw = (state, adding, typed) => {
    const head = [h('h3', { class: 'with-ico' }, icon('calendar'), t('cal_title')), h('p', { class: 'soft' }, t('cal_blurb'))];
    if (state === undefined) return calFill(box, ...head);        // until the server said which are linked
    const list = (state && state.calendars) || [];
    const full = state && list.length >= state.max;
    const items = list.length ? h('ul', { class: 'cal-items', id: 'cal-items' }, list.map(item)) : null;
    if (!(adding || !list.length) || full) {
      calFill(box, ...head, items,
        full ? h('p', { class: 'soft', id: 'cal-full' }, t('cal_full', { n: state.max }))
          : h('div', { class: 'row gap' }, h('button', { class: 'btn', id: 'cal-add', onclick: () => { draw(state, true); $('#cal-url').focus(); } }, t('cal_add'))));
      return;
    }
    const field = h('input', { type: 'url', id: 'cal-url', autocomplete: 'off', spellcheck: 'false', placeholder: 'https://calendar.google.com/calendar/ical/…/basic.ics',
      onkeydown: (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); link.click(); } } });
    const side = h('select', { id: 'cal-new-side' }, sides(typed ? typed.side : mode()));
    const name = h('input', { type: 'text', id: 'cal-new-name', maxlength: 60, autocomplete: 'off', placeholder: t('cal_new_name_ph') });
    if (typed) { field.value = typed.url; name.value = typed.name; }
    const link = h('button', { class: 'btn main', id: 'cal-link', onclick: async () => {
      if (!field.value.trim()) return field.focus();
      if (calLinking) return;
      calLinking = link.disabled = true;
      link.textContent = t('cal_checking');
      try {
        const now = await api('POST', '/api/calendars', { url: field.value, side: side.value, name: name.value });
        field.value = '';
        draw(now, false);
        const added = (now.calendars || []).find((c) => c.id === now.added);
        toast(t('cal_saved', { name: added ? added.name : '', side: t(`mode_${side.value}`) }));
      } catch (err) {
        link.disabled = false;
        link.textContent = t('cal_link');
        fail(err);
      }
      calLinking = false;
    } }, t('cal_link'));
    calFill(box, ...head, items,
      h('div', { class: 'cal-new', id: 'cal-new' },
        list.length ? h('h4', {}, t('cal_add')) : null,
        h('p', { class: 'soft' }, t('cal_where')),
        h('label', { class: 'field', for: 'cal-url' }, t('cal_label')), field,
        h('div', { class: 'cal-item-row' },
          h('div', { class: 'cal-item-field' }, h('label', { class: 'field', for: 'cal-new-side' }, t('cal_side')), side),
          h('div', { class: 'cal-item-field wide' }, h('label', { class: 'field', for: 'cal-new-name' }, t('cal_new_name')), name)),
        h('div', { class: 'row gap' }, link, list.length ? h('button', { class: 'btn', id: 'cal-cancel', onclick: () => draw(state, false) }, t('cancel')) : null)));
  };
  draw(undefined, false);
  api('GET', '/api/calendars').then((state) => draw(state, false), () => draw(null, false));
  return box;
}
SETTINGS_PARTS.push(calendarSettings);
