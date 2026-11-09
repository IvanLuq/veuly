'use strict';
// AI accounts (transcapp/accounts.py): who writes each course's notes and each project's minutes.
// Settings lists them: what Setup sets up, and any added one, with an API key of its own (Anthropic, OpenAI, or another service at its
// own address) or, under Advanced, a Claude Code or Codex sign-in of its own (Sign in opens a terminal with its own login folder);
// one choice per side (School uses, Work uses). A course or a project can choose its own on its settings page; the class page says, in one
// quiet line, who writes and where the text goes, and a note that failed because that account is signed out offers its Sign in.
// Loaded after app.js: SETTINGS_PARTS, COURSE_SETTINGS_PARTS, CLASS_NOTICES, ERROR_ACTIONS, INFO_PARTS. Gives i18n.js `currentService`, the {service} of its texts.

ICONS.person = '<circle cx="12" cy="8" r="3.5"/><path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5"/>';

const acctFill = (el, ...kids) => el.replaceChildren(...kids.flat().filter((k) => k != null && k !== false));
const acctList = () => (S.settings && Array.isArray(S.settings.claude_accounts) ? S.settings.claude_accounts : []);
const acctById = (id) => acctList().find((a) => a.id === id) || null;
// The side's choice, and the account a course writes with: as accounts.resolve on the server (a choice no longer listed
// counts as none: the side's, then this Mac's Claude Code).
const acctSide = (kind) => acctById(S.settings[kind === 'project' ? 'work_account' : 'school_account']) || null;
const acctOf = (course) => (course.claude_account === 'default' ? null : acctById(course.claude_account) || acctSide(course.kind));
// What Setup set up, as a name: it is only "this Mac's Claude Code" when that is what was chosen.
const acctDefaultNames = () => ({ 'claude-code': ['acct_default', 'acct_this_mac'], api: ['acct_setup_api', 'acct_setup_api_who'],
  openai: ['acct_setup_service', 'acct_setup_service'], codex: ['acct_default_codex', 'acct_default_codex_who'] }[S.settings && S.settings.engine] || ['acct_setup_none', 'acct_setup_none_who']);
const acctName = (a) => (a ? a.name : t(acctDefaultNames()[0], { service: serviceOf(null) }));
const acctWho = (a) => (a ? t('acct_your', { name: a.name }) : t(acctDefaultNames()[1], { service: serviceOf(null) }));
const hostOf = (url) => { try { return new URL(url).hostname; } catch (e) { return url || ''; } };
const isLocalHost = (host) => host === '127.0.0.1' || host === 'localhost';

// The service an account's text goes to, by its name (as accounts.service_of on the server); null: what Setup set up; "" when nothing is.
function serviceOf(a) {
  const how = a ? a.kind : S.settings && S.settings.engine;
  if (how === 'openai') {
    const base = a ? a.base_url : S.settings.openai_base_url;
    return !base ? 'OpenAI' : (a ? a.name : S.settings.openai_name) || hostOf(base);
  }
  if (how === 'codex') return 'Codex';
  return how ? 'Claude' : '';
}
// What a text says when it does not say which account: the course on screen's, else the side's.
window.currentService = () => {
  if (!S.settings) return '';
  const course = S.courses && S.view && S.view.slug ? S.courses.find((c) => c.slug === S.view.slug) : null;
  return serviceOf(course ? acctOf(course) : acctSide(mode()));
};
// Where the text goes: the company, or the service's name and its host.
function serviceWhere(a) {
  const how = a ? a.kind : S.settings.engine;
  if (how === 'openai') {
    const base = a ? a.base_url : S.settings.openai_base_url;
    if (!base) return 'OpenAI';
    const host = hostOf(base), name = (a ? a.name : S.settings.openai_name) || host;
    if (isLocalHost(host)) return t('where_local', { name, host });
    return name === host ? host : `${name} (${host})`;
  }
  if (how === 'codex') return t('where_through', { company: 'OpenAI', tool: 'Codex' });
  if (how === 'login' || how === 'claude-code') return t('where_through', { company: 'Anthropic', tool: 'Claude Code' });
  if (how === 'api' && !a && S.settings.api_base_url && hostOf(S.settings.api_base_url) !== 'api.anthropic.com') return hostOf(S.settings.api_base_url);
  return how ? 'Anthropic' : '';
}
// The small model of a heads-up, as headsup.engine_settings picks it (Codex's is what its login lists: that is known only on the server).
function acctSmall(a) {
  const how = a ? a.kind : S.settings.engine;
  if (how === 'openai') {
    const base = a ? a.base_url : S.settings.openai_base_url, small = a ? a.small_model : S.settings.openai_small_model;
    return small || (base ? ((a ? a.model : S.settings.openai_model) || serviceOf(a)) : 'gpt-5-mini');
  }
  return how === 'codex' ? 'Codex' : 'Claude Haiku';
}

// A terminal on `claude auth login` with the account's own folder; when none opens, the line to type goes in `out`.
async function acctSignIn(a, out) {
  const r = await api('POST', `/api/accounts/${a.id}/login`);
  if (r.ok) return toast(t('acct_sign_in_next', { name: a.name }));
  if (out) acctFill(out, h('div', { class: 'notice bad' }, r.command ? [t('sign_in_run'), ' ', h('code', {}, r.command)] : r.message));
  else toast(r.command ? `${t('sign_in_run')} ${r.command}` : r.message);
}

const acctStatus = (a) => {
  if (a.kind === 'api') return t('acct_kind_api', { hint: a.key_hint || '' });
  if (a.kind === 'openai') {
    if (!a.base_url) return t('acct_kind_openai', { hint: a.key_hint || '' });
    return t(a.key_hint ? 'acct_kind_service' : 'acct_kind_service_nokey', { host: hostOf(a.base_url), hint: a.key_hint || '' });
  }
  if (a.kind === 'codex') {
    if (a.signed === undefined) return `${t('acct_kind_codex')}.`;
    return `${t('acct_kind_codex')}. ${a.signed === true ? t('signed_in') : a.signed === false ? t('signed_out') : t('acct_cant_tell')}`;
  }
  const who = [a.email, a.org].filter(Boolean).join(' · ');
  if (a.signed === undefined) return `${t('acct_kind_login')}.`;      // until the server said (Settings asks it as it opens)
  const state = a.signed === true ? (who ? t('acct_signed_as', { who }) : t('signed_in')) : a.signed === false ? t('signed_out') : t('acct_cant_tell');
  return `${t('acct_kind_login')}. ${state}`;
};

// ---------------------------------------------------------------- Settings
let acctBusy = false;
function accountsSettings() {
  const box = h('div', { class: 'sheet', id: 'claude-accounts' });
  const out = h('div', { id: 'acct-out', 'aria-live': 'polite' });
  let state = null, adding = false, renaming = '';
  // a redraw replaces the button that had the keyboard: give it to the one that takes its place
  const focusOn = (sel) => setTimeout(() => { const el = $(sel); if (el) el.focus(); }, 0);
  const act = (fn) => async (e) => {
    const btn = e.currentTarget;
    if (acctBusy) return;
    acctBusy = btn.disabled = true;
    try { await fn(); } catch (err) { fail(err); }
    acctBusy = false;
    if (btn.isConnected) btn.disabled = false;
  };
  const refresh = async (again) => {
    state = await api('GET', again ? '/api/accounts?refresh=1' : '/api/accounts');
    draw();
  };
  const afterChange = async (answer) => {       // the list lives in settings.json: the rest of the page reads S.settings
    state = answer;
    await loadState();
    draw();
  };
  const test = (a) => act(async () => {
    acctFill(out, h('div', { class: 'notice' }, t('testing')));
    try {
      const r = await api('POST', '/api/setup/test-engine', { account: a ? a.id : 'default' });
      acctFill(out, h('div', { class: 'notice good' }, `${acctName(a)}: ${t('test_ok', { model: r.model })}`));
    } catch (err) {
      acctFill(out, h('div', { class: 'notice bad' }, `${acctName(a)}: `, HELP(err.kind), ' ', h('span', { class: 'soft' }, err.message)));
    }
  });
  const remove = (a) => confirmPop({ id: 'acct-remove-pop', title: t('acct_remove_q', { name: a.name }), body: t('acct_remove_body'),
    yes: t('remove'), tick: a.kind === 'login' ? { label: t('acct_signout'), checked: true } : a.kind === 'codex' ? { label: t('acct_signout_codex'), checked: true } : null,
    run: async (_, __, signout) => {
      try {
        const r = await api('DELETE', `/api/accounts/${a.id}${signout ? '?signout=1' : ''}`);
        await afterChange(r);
        focusOn('#acct-add');
        toast(t('acct_removed', { name: a.name }));
      } catch (err) { fail(err); }
    } });

  const defaultRow = () => {
    const s = S.setup || {}, engine = S.settings.engine;
    const line = engine === 'api' ? t('acct_default_api') : engine === 'openai' ? t('acct_default_openai') : engine === 'codex'
      ? `${t('acct_default_codex_line')} ${s.codex_signed === true ? t('signed_in') : s.codex_signed === false ? t('signed_out') : ''}` : engine === 'claude-code'
        ? `${t('acct_default_cc')} ${s.logged_in === true ? t('signed_in') : s.logged_in === false ? t('signed_out') : ''}` : t('acct_default_none');
    return h('li', { class: 'acct-row', id: 'acct-default' },
      h('div', { class: 'acct-text' }, h('strong', {}, acctName(null)), h('span', { class: 'soft' }, line.trim())),
      h('span', { class: 'acct-side' },
        engine ? h('button', { class: 'btn', id: 'acct-test-default', onclick: test(null) }, t('acct_test')) : null,
        h('button', { class: 'btn quiet', onclick: () => { const el = $('#setup-section'); if (el) el.scrollIntoView(); } }, t('open_setup'))));
  };
  // The model it writes with: the one chosen in Setup, or its own. One no longer on the list stays what it says.
  const modelPick = (a) => {
    if (a.kind === 'openai' || a.kind === 'codex') {      // any name may do: typed
      const put = async (e, field) => {
        try {
          await afterChange(await api('PUT', `/api/accounts/${a.id}`, field));
          focusOn(`#${e.target.id}`);
          toast(t('saved'));
        } catch (err) { fail(err); draw(); }
      };
      return h('div', { class: 'acct-typed' },
        h('label', { class: 'acct-model' }, h('span', { class: 'soft' }, t('model_label')),
          h('input', { type: 'text', id: `acct-model-${a.id}`, value: a.model || '', autocomplete: 'off', placeholder: a.kind === 'codex' ? t('codex_model_ph') : a.base_url ? '' : t('openai_model_ph'),
            onchange: (e) => put(e, { model: e.target.value.trim() }) })),
        h('details', {}, h('summary', { class: 'soft' }, t('service_more')),
          h('label', { class: 'acct-model' }, h('span', { class: 'soft' }, t('service_small')),
            h('input', { type: 'text', id: `acct-small-${a.id}`, value: a.small_model || '', autocomplete: 'off', onchange: (e) => put(e, { small_model: e.target.value.trim() }) })),
          h('label', { class: 'acct-model' }, h('span', { class: 'soft' }, t('service_context')),
            h('input', { type: 'text', inputmode: 'numeric', id: `acct-context-${a.id}`, value: a.context_tokens || '', autocomplete: 'off', placeholder: t('service_context_ph'),
              onchange: (e) => put(e, { context_tokens: e.target.value.trim() ? Number(e.target.value) : 0 }) }))));
    }
    const known = a.kind === 'api'
      ? [['claude-sonnet-5-5', t('m_api_sonnet')], ['claude-haiku-5-5', t('m_api_haiku')], ['claude-opus-5-5', t('m_api_opus')]]
      : [['sonnet', t('m_sonnet')], ['haiku', t('m_haiku')], ['opus', t('m_opus')]];
    const mine = a.model || '';
    const options = [['', t('acct_model_same')], ...known, ...(mine && !known.some(([v]) => v === mine) ? [[mine, mine]] : [])];
    return h('label', { class: 'acct-model' }, h('span', { class: 'soft' }, t('model_label')),
      h('select', { id: `acct-model-${a.id}`, onchange: async (e) => {
        try {
          await afterChange(await api('PUT', `/api/accounts/${a.id}`, { model: e.target.value }));
          focusOn(`#acct-model-${a.id}`);
          toast(t('saved'));
        } catch (err) { fail(err); draw(); }
      } }, options.map(([v, label]) => h('option', { value: v, selected: v === mine }, label))));
  };
  const row = (a) => {
    if (renaming === a.id) {
      const field = h('input', { type: 'text', id: `acct-name-${a.id}`, value: a.name, maxlength: 40, 'aria-label': t('acct_name'),
        onkeydown: (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); save.click(); } if (e.key === 'Escape' && !e.isComposing) { e.preventDefault(); renaming = ''; draw(); focusOn(`#acct-rename-${a.id}`); } } });
      const save = h('button', { class: 'btn main', id: `acct-save-${a.id}`, onclick: act(async () => {
        const r = await api('PUT', `/api/accounts/${a.id}`, { name: field.value });
        renaming = '';
        await afterChange(r);
        focusOn(`#acct-rename-${a.id}`);
        toast(t('saved'));
      }) }, t('acct_save'));
      setTimeout(() => field.isConnected && field.focus(), 0);
      return h('li', { class: 'acct-row', id: `acct-${a.id}` },
        h('div', { class: 'acct-text' }, field),
        h('span', { class: 'acct-side' }, save, h('button', { class: 'btn', onclick: () => { renaming = ''; draw(); focusOn(`#acct-rename-${a.id}`); } }, t('cancel'))));
    }
    const login = a.kind === 'login', codex = a.kind === 'codex';
    return h('li', { class: 'acct-row', id: `acct-${a.id}` },
      h('div', { class: 'acct-text' },
        h('strong', {}, a.name),
        h('span', { class: `soft${(login || codex) && a.signed === false ? ' warn' : ''}`, id: `acct-status-${a.id}` },
          login && state && !state.claude ? `${t('acct_kind_login')}. ${t('acct_no_claude')}`
            : codex && state && !state.codex ? `${t('acct_kind_codex')}. ${t('acct_no_codex')}` : acctStatus(a)),
        modelPick(a)),
      h('span', { class: 'acct-side' },
        ((login && state && state.claude) || (codex && state && state.codex)) && a.signed !== true
          ? h('button', { class: 'btn main', id: `acct-signin-${a.id}`, onclick: act(() => acctSignIn(a, out)) }, t('sign_in')) : null,
        h('button', { class: 'btn', id: `acct-test-${a.id}`, onclick: test(a) }, t('acct_test')),
        h('button', { class: 'btn quiet', id: `acct-rename-${a.id}`, onclick: () => { renaming = a.id; draw(); } }, t('acct_rename')),
        h('button', { class: 'btn quiet', id: `acct-remove-${a.id}`, onclick: () => remove(a) }, t('remove'))));
  };

  const form = () => {
    const name = h('input', { type: 'text', id: 'acct-new-name', maxlength: 40, placeholder: t('acct_name_ph'), autocomplete: 'off' });
    const key = h('input', { type: 'password', id: 'acct-new-key', placeholder: 'sk-ant-…', autocomplete: 'off', 'aria-label': t('api_key') });
    const keyRow = h('div', { id: 'acct-key-row' }, h('label', { class: 'field', for: 'acct-new-key' }, t('api_key')), key);
    const url = h('input', { type: 'text', id: 'acct-new-url', placeholder: 'https://openrouter.ai/api', autocomplete: 'off' });
    const model = h('input', { type: 'text', id: 'acct-new-model', autocomplete: 'off' });
    const serviceRow = h('div', { id: 'acct-service-row', hidden: true },
      h('label', { class: 'field', for: 'acct-new-url' }, t('service_address'), h('small', {}, t('service_address_help'))), url,
      h('label', { class: 'field', for: 'acct-new-model' }, t('service_model'), h('small', {}, t('service_model_help'))), model);
    const keyHelp = h('small', { id: 'acct-key-help', hidden: true }, t('service_key_help'));
    keyRow.firstChild.append(keyHelp);
    const how = (value, label, help) => h('label', { class: 'option' },
      h('input', { type: 'radio', name: 'acct-kind', value, checked: value === 'api', onchange: () => {
        keyRow.hidden = value === 'login' || value === 'codex';
        serviceRow.hidden = keyHelp.hidden = value !== 'service';
        key.placeholder = value === 'api' ? 'sk-ant-…' : value === 'openai' ? 'sk-…' : '';
      } }),
      h('strong', {}, label), h('span', {}, help));
    const add = h('button', { class: 'btn main', id: 'acct-add-yes', onclick: act(async () => {
      if (!name.value.trim()) return name.focus();
      const way = $('input[name="acct-kind"]:checked', box).value, service = way === 'service';
      if (service && !url.value.trim()) return url.focus();
      if (service && !model.value.trim()) return model.focus();
      const kind = service ? 'openai' : way;
      const r = await api('POST', '/api/accounts', { name: name.value, kind, key: kind === 'api' || kind === 'openai' ? key.value : '',
        ...(service ? { base_url: url.value, model: model.value } : {}) });
      adding = false;
      await afterChange(r);
      toast(t(way === 'login' || way === 'codex' ? 'acct_added_login' : 'acct_added_api', { name: name.value.trim() }));
      const next = $(`#acct-signin-${r.added}`) || $(`#acct-test-${r.added}`);
      if (next) next.focus();
    }) }, t('acct_add_yes'));
    setTimeout(() => name.isConnected && name.focus(), 0);
    return h('div', { class: 'acct-form', id: 'acct-form' },
      h('h4', {}, t('acct_add')),
      h('label', { class: 'field', for: 'acct-new-name' }, t('acct_name'), h('small', {}, t('acct_name_help'))), name,
      h('fieldset', { class: 'question' }, h('legend', {}, t('acct_how')),
        h('div', { class: 'options' }, how('api', t('acct_how_api'), t('acct_how_api_d')), how('openai', t('acct_how_openai'), t('acct_how_openai_d')),
          how('service', t('acct_how_service'), t('acct_how_service_d'))),
        h('details', { id: 'acct-advanced' }, h('summary', {}, t('acct_advanced')),
          h('div', { class: 'options' }, how('login', t('acct_how_login'), t('acct_how_login_d')), how('codex', t('acct_how_codex'), t('acct_how_codex_d'))))),
      serviceRow,
      keyRow,
      h('div', { class: 'row gap' }, add, h('button', { class: 'btn', id: 'acct-add-no', onclick: () => { adding = false; draw(); focusOn('#acct-add'); } }, t('cancel'))));
  };

  const sides = () => {
    const pick = (kind, key) => h('div', { class: 'acct-pick' },
      h('label', { class: 'field', for: `acct-${kind}` }, t(`acct_uses_${kind}`)),
      h('select', { id: `acct-${kind}`, onchange: async (e) => {
        try { S.settings = await api('POST', '/api/settings', { [key]: e.target.value }); toast(t('saved')); } catch (err) { fail(err); }
      } },
      h('option', { value: 'default', selected: !acctById(S.settings[key]) }, acctName(null)),
      acctList().map((a) => h('option', { value: a.id, selected: S.settings[key] === a.id }, a.name))));
    return h('div', { class: 'acct-sides' }, pick('school', 'school_account'), pick('work', 'work_account'));
  };

  const draw = () => {
    const accts = state ? state.accounts : acctList();
    acctFill(box,
      h('h3', { class: 'with-ico' }, icon('person'), t('acct_title')),
      h('p', { class: 'soft' }, t('acct_blurb')),
      h('ul', { class: 'acct-list' }, defaultRow(), accts.map(row)),
      out,
      adding ? form() : h('div', { class: 'row gap' },
        h('button', { class: 'btn', id: 'acct-add', onclick: () => { adding = true; draw(); } }, t('acct_add')),
        accts.some((a) => a.kind === 'login' || a.kind === 'codex') ? h('button', { class: 'btn quiet', id: 'acct-check', onclick: act(() => refresh(true)) }, t('check_again')) : null),
      acctList().length ? [sides(), h('p', { class: 'soft' }, t('acct_sides_help'))] : null);
  };
  draw();
  refresh(false).catch(() => {});
  return box;
}
SETTINGS_PARTS.push(accountsSettings);

// ---------------------------------------------------------------- a course's or a project's settings
function accountCourse(course) {
  const box = h('div', { class: 'sheet', id: 'course-account' });
  const out = h('div', { id: 'course-acct-out' });
  const side = acctSide(course.kind);
  const draw = (state) => {
    const mine = acctOf(course);
    const listed = state && mine ? state.accounts.find((a) => a.id === mine.id) : null;
    const signedOut = listed && (listed.kind === 'login' || listed.kind === 'codex') && listed.signed === false;
    const head = [h('h3', { class: 'with-ico' }, icon('person'), t('acct_course_title')), h('p', { class: 'soft' }, t('acct_course_help'))];
    if (!acctList().length) {
      return acctFill(box, ...head, h('p', { class: 'soft', id: 'course-acct-line' }, t('acct_course_will', { who: acctWho(null) }), ' ', t('acct_none_yet')),
        h('div', { class: 'row' }, h('button', { class: 'btn', id: 'course-acct-add', onclick: () => go({ name: 'settings', focus: 'claude-accounts' }).catch(fail) }, t('acct_open_settings'))));
    }
    const choice = course.claude_account === 'default' || acctById(course.claude_account) ? course.claude_account : '';
    acctFill(box, ...head,
      h('label', { class: 'field', for: 'c-account' }, t('acct_course_label')),
      h('select', { id: 'c-account', onchange: async (e) => {
        try {
          await api('PUT', `/api/courses/${course.slug}`, { claude_account: e.target.value });
          await loadState();
          course = S.courses.find((c) => c.slug === course.slug) || course;
          draw(state);
          toast(t('saved'));
        } catch (err) { fail(err); }
      } },
      h('option', { value: '', selected: choice === '' }, t('acct_same', { name: acctName(side) })),
      h('option', { value: 'default', selected: choice === 'default' }, acctName(null)),
      acctList().map((a) => h('option', { value: a.id, selected: choice === a.id }, a.name))),
      h('p', { class: 'soft', id: 'course-acct-line' }, t('acct_course_will', { who: acctWho(mine) })),
      signedOut ? h('div', { class: 'notice', id: 'course-acct-signin' }, t('acct_signed_out', { name: mine.name }), ' ',
        h('button', { class: 'btn', onclick: async (e) => { e.currentTarget.disabled = true; try { await acctSignIn(mine, out); } catch (err) { fail(err); } } },
          t('acct_sign_in_to', { name: mine.name }))) : null,
      out);
  };
  draw(null);
  if (acctList().some((a) => a.kind === 'login' || a.kind === 'codex')) api('GET', '/api/accounts').then(draw, () => {});
  return box;
}
COURSE_SETTINGS_PARTS.push(accountCourse);

// ---------------------------------------------------------------- the class page
// One quiet line: who writes (or wrote) this note and where its text goes. With nothing chosen yet, nothing goes anywhere: no line.
function accountNotice(c) {
  const course = S.courses.find((k) => k.slug === S.view.slug);
  if (!course) return null;
  const wrote = c.account === 'default' ? null : acctById(c.account);
  if (c.status === 'done') {
    if (!c.account || (c.account !== 'default' && !wrote)) return null;       // written before accounts, or by one removed since
    return h('p', { class: 'soft acct-line no-print', id: 'acct-class-line' }, t('acct_class_done', { who: acctWho(wrote) }), ' ', t('acct_went_to', { where: serviceWhere(wrote) }));
  }
  const mine = acctOf(course);
  if (!serviceOf(mine)) return null;
  return h('p', { class: 'soft acct-line no-print', id: 'acct-class-line' }, t('acct_class_will', { who: acctWho(mine) }), ' ', t('acct_goes_to', { where: serviceWhere(mine) }));
}
CLASS_NOTICES.push(accountNotice);

// Info says where the text goes, from the course on screen or the side.
INFO_PARTS.push(() => {
  const course = S.courses && S.view && S.view.slug ? S.courses.find((c) => c.slug === S.view.slug) : null;
  const acct = course ? acctOf(course) : acctSide(mode());
  return [h('h3', {}, t('info_goes')), h('p', { id: 'info-goes' }, serviceOf(acct) ? t('info_goes_text', { where: serviceWhere(acct) }) : t('info_goes_none'))];
});

// A note that failed because its account is signed out (account_signed_out, the server's text names the account):
// its own Sign in, beside Try again.
function accountSignInAction(c) {
  const wrote = acctById(c.account);
  if (c.error_kind !== 'account_signed_out' || !wrote || (wrote.kind !== 'login' && wrote.kind !== 'codex')) return null;
  return h('button', { class: 'btn', id: 'acct-class-signin', onclick: async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try { await acctSignIn(wrote, null); } catch (err) { fail(err); }
    btn.disabled = false;
  } }, t('acct_sign_in_to', { name: wrote.name }));
}
ERROR_ACTIONS.push(accountSignInAction);
