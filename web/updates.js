// A newer version of Veuly, for a copy built with an address to ask (transcapp/updates.py): a tile with a link, nothing more.
// The app downloads and installs nothing by itself. Loaded after app.js: HOME_TILES, INFO_PARTS.
(function () {
  HOME_TILES.push((home) => {
    if (!home.update) return null;
    return h('section', { class: 'tile', id: 'home-update' },
      h('h2', {}, t('update_title', { version: home.update.version })),
      h('p', {}, t('update_text')),
      h('a', { class: 'btn', href: home.update.url, target: '_blank', rel: 'noopener noreferrer' }, t('update_get')));
  });
  INFO_PARTS.push(() => [h('h3', {}, t('info_update')), h('p', {}, t('info_update_text'))]);
})();
