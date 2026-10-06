// Catalog mode (showcase/, rules SC25–SC33): the gallery, the piece viewer and
// the slideshow, built from the JSON the page embeds (#catalog-data). Plain JS,
// no build step. Works by touch, mouse and keyboard alike, on any modern
// browser; device niceties (wake lock, full screen, offline) are used where
// they exist and skipped silently where they don't.
//
// Keys: arrows move between photos (left/right) and pieces (up/down) in the
// viewer, Esc closes it; in the slideshow Space pauses, any other key or a
// touch opens the piece on screen.
(function () {
  'use strict';

  var data = JSON.parse(document.getElementById('catalog-data').textContent);
  var pieces = data.pieces || [];
  var reducedMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var $ = function (id) { return document.getElementById(id); };
  var grid = $('catalog-grid');
  var chips = $('catalog-chips');
  var viewerEl = $('viewer');
  var cinemaEl = $('cinema');

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function byId(id) {
    for (var i = 0; i < pieces.length; i++) if (pieces[i].id === id) return pieces[i];
    return null;
  }

  // ---------------------------------------------------------------- gallery
  var activeCategory = null;

  function visiblePieces() {
    return pieces.filter(function (p) {
      return activeCategory === null || p.category_id === activeCategory;
    });
  }

  function renderChips() {
    if (!data.categories || !data.categories.length) return;
    chips.hidden = false;
    var options = [{ id: null, label: 'All' }].concat(data.categories);
    options.forEach(function (option) {
      var button = el('button', 'catalog__chip', option.label);
      button.type = 'button';
      button.setAttribute('aria-pressed', option.id === activeCategory ? 'true' : 'false');
      button.addEventListener('click', function () {
        activeCategory = option.id;
        chips.querySelectorAll('.catalog__chip').forEach(function (chip) {
          chip.setAttribute('aria-pressed', chip === button ? 'true' : 'false');
        });
        renderGrid();
      });
      chips.appendChild(button);
    });
  }

  function renderGrid() {
    grid.textContent = '';
    var shown = visiblePieces();
    $('catalog-empty').hidden = pieces.length > 0;
    $('catalog-play').hidden = pieces.length === 0;
    shown.forEach(function (piece) {
      var tile = el('button', 'catalog__tile');
      tile.type = 'button';
      var cover = el('span', 'catalog__cover');
      var img = el('img');
      img.src = piece.photos[0].thumb;
      img.alt = '';
      img.loading = 'lazy';
      cover.appendChild(img);
      tile.appendChild(cover);
      var caption = el('span', 'catalog__caption');
      caption.appendChild(el('span', 'catalog__tile-title', piece.title));
      if (piece.category) caption.appendChild(el('span', 'catalog__tile-category', piece.category));
      tile.appendChild(caption);
      tile.addEventListener('click', function () { openViewer(piece.id, 0); });
      grid.appendChild(tile);
    });
  }

  // ----------------------------------------------------------------- viewer
  var view = { piece: null, photo: 0, opener: null };

  function openViewer(pieceId, photoIndex) {
    var piece = byId(pieceId);
    if (!piece) return;
    if (viewerEl.hidden) view.opener = document.activeElement;
    view.piece = piece;
    view.photo = photoIndex || 0;
    $('viewer-category').textContent = piece.category;
    $('viewer-title').textContent = piece.title;
    $('viewer-text').textContent = piece.description;
    var specs = $('viewer-specs');
    specs.textContent = '';
    piece.specs.forEach(function (pair) {
      specs.appendChild(el('dt', null, pair[0]));
      specs.appendChild(el('dd', null, pair[1]));
    });
    var dots = $('viewer-dots');
    dots.textContent = '';
    piece.photos.forEach(function () { dots.appendChild(el('span', 'viewer__dot')); });
    var several = piece.photos.length > 1;
    $('viewer-prev').hidden = !several;
    $('viewer-next').hidden = !several;
    dots.hidden = !several;
    var others = visiblePieces().length > 1;
    $('viewer-prev-piece').hidden = !others;
    $('viewer-next-piece').hidden = !others;
    showPhoto();
    viewerEl.hidden = false;
    document.body.classList.add('is-viewing');
    $('viewer-close').focus({ preventScroll: true });
  }

  function showPhoto() {
    var photo = view.piece.photos[view.photo];
    var img = $('viewer-photo');
    img.src = photo.full;
    img.alt = view.piece.title + (view.piece.photos.length > 1
      ? ', photo ' + (view.photo + 1) + ' of ' + view.piece.photos.length : '');
    $('viewer-dots').querySelectorAll('.viewer__dot').forEach(function (dot, i) {
      dot.classList.toggle('is-current', i === view.photo);
    });
    // Warm the next photo so a swipe doesn't wait on the network.
    var next = view.piece.photos[(view.photo + 1) % view.piece.photos.length];
    if (next) new Image().src = next.full;
  }

  function stepPhoto(delta) {
    var count = view.piece.photos.length;
    view.photo = (view.photo + delta + count) % count;
    showPhoto();
  }

  function stepPiece(delta) {
    var list = visiblePieces();
    var index = list.indexOf(view.piece);
    if (index === -1) index = 0;
    openViewer(list[(index + delta + list.length) % list.length].id, 0);
  }

  function closeViewer() {
    viewerEl.hidden = true;
    document.body.classList.remove('is-viewing');
    if (view.opener && view.opener.focus) view.opener.focus({ preventScroll: true });
  }

  $('viewer-prev').addEventListener('click', function () { stepPhoto(-1); });
  $('viewer-next').addEventListener('click', function () { stepPhoto(1); });
  $('viewer-prev-piece').addEventListener('click', function () { stepPiece(-1); });
  $('viewer-next-piece').addEventListener('click', function () { stepPiece(1); });
  $('viewer-close').addEventListener('click', closeViewer);

  // Swipe between photos: a mostly horizontal drag of 40px or more.
  var swipe = null;
  $('viewer-stage').addEventListener('pointerdown', function (e) {
    swipe = { x: e.clientX, y: e.clientY };
  });
  $('viewer-stage').addEventListener('pointerup', function (e) {
    if (!swipe || !view.piece || view.piece.photos.length < 2) { swipe = null; return; }
    var dx = e.clientX - swipe.x, dy = e.clientY - swipe.y;
    swipe = null;
    if (Math.abs(dx) >= 40 && Math.abs(dx) > Math.abs(dy)) stepPhoto(dx < 0 ? 1 : -1);
  });

  // ---------------------------------------------------------------- cinema
  var SLIDE_MS = 7000;
  var cinema = { on: false, slides: [], index: -1, timer: null, paused: false,
                 front: $('cinema-a'), back: $('cinema-b') };

  function shuffle(list) {
    for (var i = list.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var swap = list[i]; list[i] = list[j]; list[j] = swap;
    }
    return list;
  }

  function buildSlides() {
    // Pieces in random order, each piece's photos together, so a caption
    // stays with its piece for a few slides rather than flickering.
    var slides = [];
    shuffle(visiblePieces().slice()).forEach(function (piece) {
      piece.photos.forEach(function (photo, i) {
        slides.push({ piece: piece, photo: i, src: photo.full });
      });
    });
    return slides;
  }

  function showSlide() {
    if (!cinema.on || !cinema.slides.length) return;
    cinema.index = (cinema.index + 1) % cinema.slides.length;
    if (cinema.index === 0 && cinema.slides.length > 1) cinema.slides = buildSlides();
    var slide = cinema.slides[cinema.index];
    var layer = cinema.back;
    var img = layer.querySelector('img');
    var ready = function () {
      if (!cinema.on) return;
      layer.className = 'cinema__slide is-visible' +
        (reducedMotion ? '' : ' kb-' + (1 + Math.floor(Math.random() * 4)));
      cinema.front.classList.remove('is-visible');
      cinema.back = cinema.front;
      cinema.front = layer;
      $('cinema-title').textContent = slide.piece.title;
      $('cinema-category').textContent = slide.piece.category;
      schedule();
    };
    img.onload = ready;
    img.onerror = function () { schedule(); };
    img.src = slide.src;
    if (img.complete && img.naturalWidth) { img.onload = null; ready(); }
  }

  function schedule() {
    clearTimeout(cinema.timer);
    if (!cinema.paused) cinema.timer = setTimeout(showSlide, SLIDE_MS);
  }

  function startCinema() {
    if (cinema.on || !pieces.length) return;
    if (!viewerEl.hidden) closeViewer();
    cinema.slides = buildSlides();
    if (!cinema.slides.length) return;
    cinema.on = true;
    cinema.paused = false;
    cinema.index = -1;
    $('cinema-paused').hidden = true;
    cinemaEl.hidden = false;
    document.body.classList.add('is-cinema');
    showSlide();
  }

  function stopCinema(openCurrent) {
    if (!cinema.on) return;
    cinema.on = false;
    clearTimeout(cinema.timer);
    cinemaEl.hidden = true;
    document.body.classList.remove('is-cinema');
    [cinema.front, cinema.back].forEach(function (layer) { layer.className = 'cinema__slide'; });
    var slide = cinema.slides[cinema.index];
    if (openCurrent && slide) openViewer(slide.piece.id, slide.photo);
    resetIdle();
  }

  function togglePause() {
    cinema.paused = !cinema.paused;
    $('cinema-paused').hidden = !cinema.paused;
    cinemaEl.classList.toggle('is-paused', cinema.paused);
    schedule();
  }

  $('catalog-play').addEventListener('click', startCinema);
  // Any touch or click on the slideshow opens the piece on screen: "tell me
  // about this one".
  var swallowClicksUntil = 0;
  cinemaEl.addEventListener('pointerdown', function (e) {
    e.preventDefault();
    // The same touch ends in a click on whatever the viewer puts under the
    // finger (a close or next button); that click isn't a decision.
    swallowClicksUntil = Date.now() + 500;
    stopCinema(true);
  });
  document.addEventListener('click', function (e) {
    if (Date.now() < swallowClicksUntil) { e.stopPropagation(); e.preventDefault(); }
  }, true);

  // ------------------------------------------------------------------ idle
  var idleTimer = null;
  function resetIdle() {
    clearTimeout(idleTimer);
    var minutes = Number(data.idleMinutes) || 0;
    if (minutes > 0 && pieces.length) idleTimer = setTimeout(startCinema, minutes * 60000);
  }
  ['pointerdown', 'keydown', 'wheel', 'touchmove'].forEach(function (type) {
    document.addEventListener(type, function () { if (!cinema.on) resetIdle(); },
      { passive: true });
  });
  window.addEventListener('scroll', function () { if (!cinema.on) resetIdle(); }, { passive: true });

  // -------------------------------------------------------------- keyboard
  document.addEventListener('keydown', function (e) {
    if (cinema.on) {
      if (e.key === ' ' || e.key === 'Spacebar') { e.preventDefault(); togglePause(); return; }
      if (e.key === 'Escape') { stopCinema(false); return; }
      stopCinema(true);
      return;
    }
    if (viewerEl.hidden) return;
    if (e.key === 'Escape') closeViewer();
    else if (e.key === 'ArrowRight') stepPhoto(1);
    else if (e.key === 'ArrowLeft') stepPhoto(-1);
    else if (e.key === 'ArrowDown') { e.preventDefault(); stepPiece(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); stepPiece(-1); }
  });

  // -------------------------------------------------- stay awake (SC32)
  var wakeLock = null;
  function keepAwake() {
    if (!('wakeLock' in navigator) || document.visibilityState !== 'visible') return;
    navigator.wakeLock.request('screen').then(function (lock) { wakeLock = lock; })
      .catch(function () {});
  }
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible' && (!wakeLock || wakeLock.released)) keepAwake();
  });
  keepAwake();

  // ------------------------------------------------------ full screen (SC32)
  var root = document.documentElement;
  var request = root.requestFullscreen || root.webkitRequestFullscreen;
  var exit = document.exitFullscreen || document.webkitExitFullscreen;
  var fsButton = $('catalog-fullscreen');
  if (request && (document.fullscreenEnabled || document.webkitFullscreenEnabled)) {
    fsButton.hidden = false;
    fsButton.addEventListener('click', function () {
      var current = document.fullscreenElement || document.webkitFullscreenElement;
      if (current) exit.call(document);
      else request.call(root);
    });
    var label = function () {
      fsButton.textContent = (document.fullscreenElement || document.webkitFullscreenElement)
        ? 'Exit full screen' : 'Full screen';
    };
    document.addEventListener('fullscreenchange', label);
    document.addEventListener('webkitfullscreenchange', label);
  }

  // ---------------------------------------------------------- offline (SC31)
  var offline = $('catalog-offline');
  function showConnection() { offline.hidden = navigator.onLine !== false; }
  window.addEventListener('online', showConnection);
  window.addEventListener('offline', showConnection);
  showConnection();

  if (data.serviceWorker && 'serviceWorker' in navigator) {
    navigator.serviceWorker.register(data.serviceWorker).then(function () {
      return navigator.serviceWorker.ready;
    }).then(function (registration) {
      var urls = [location.pathname];
      document.querySelectorAll('link[rel="stylesheet"], script[src], link[rel="manifest"]')
        .forEach(function (node) { urls.push(node.href || node.src); });
      if (data.logo) urls.push(data.logo);
      pieces.forEach(function (piece) {
        piece.photos.forEach(function (photo) { urls.push(photo.thumb, photo.full); });
      });
      if (registration.active) registration.active.postMessage({ type: 'cache', urls: urls });
    }).catch(function () {});
  }

  renderChips();
  renderGrid();
  resetIdle();
})();
