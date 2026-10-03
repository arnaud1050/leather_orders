/*
 * Settings → Invoicing → Invoice appearance.
 *
 * Enhances markup that already works on its own (a file input with an
 * Upload button, two radio buttons, a hex text field):
 *
 *  - the logo uploads as soon as a file is chosen or dropped, after the
 *    same type and size checks the server makes, so a refusal shows up
 *    beside the tile instead of after a round trip;
 *  - the layout cards' thumbnails and the logo tile follow the colour as
 *    it changes, through the section's --look-primary / --look-on-primary;
 *  - clicking a colour swatch (the accent colour, or the footer's
 *    background and text) opens a picker — a saturation / brightness area
 *    and a hue bar, both usable by pointer and keyboard — beside the hex
 *    box, which stays typeable;
 *  - the footer fields show only while the footer is switched on, and the
 *    thumbnails show its band.
 *
 * Only the hex field is ever submitted, and the server checks it again —
 * nothing here is trusted.
 */
(function () {
  'use strict';

  var root = document.querySelector('[data-invoice-look]');
  if (!root) return;
  root.classList.add('is-enhanced');

  // --- Colour maths --------------------------------------------------------

  var HEX = /^#[0-9a-f]{6}$/;

  function clamp(n) {
    return Math.min(1, Math.max(0, n));
  }

  function hexToRgb(hex) {
    return [1, 3, 5].map(function (i) { return parseInt(hex.slice(i, i + 2), 16); });
  }

  function rgbToHex(rgb) {
    return '#' + rgb.map(function (c) {
      return Math.round(c).toString(16).padStart(2, '0');
    }).join('');
  }

  function rgbToHsv(rgb, previousHue) {
    var r = rgb[0] / 255, g = rgb[1] / 255, b = rgb[2] / 255;
    var max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    var h = previousHue || 0;
    if (d) {
      if (max === r) h = 60 * (((g - b) / d) % 6);
      else if (max === g) h = 60 * ((b - r) / d + 2);
      else h = 60 * ((r - g) / d + 4);
      if (h < 0) h += 360;
    }
    // A grey has no hue; keeping the previous one stops the hue handle
    // jumping back to red when the saturation is dragged to zero.
    return { h: h, s: max ? d / max : 0, v: max };
  }

  function hsvToRgb(h, s, v) {
    var c = v * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = v - c;
    var parts = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x]
      : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
    return parts.map(function (p) { return (p + m) * 255; });
  }

  // Same rule as Branding.on_primary (billing/documents.py): white on a
  // dark band, near-black on a pale one.
  function textOn(hex) {
    var lum = hexToRgb(hex).map(function (c) {
      c /= 255;
      return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    });
    var l = 0.2126 * lum[0] + 0.7152 * lum[1] + 0.0722 * lum[2];
    return l > 0.179 ? '#1c1a17' : '#ffffff';
  }

  // --- Logo upload --------------------------------------------------------

  var errorBox = root.querySelector('[data-logo-error]');

  function showError(message) {
    errorBox.textContent = message || '';
    errorBox.hidden = !message;
  }

  root.querySelectorAll('[data-logo-upload]').forEach(function (form) {
    var input = form.querySelector('input[type=file]');
    var maxBytes = parseInt(input.dataset.maxBytes, 10);

    function acceptable(file) {
      var name = (file.name || '').toLowerCase();
      // Some systems report no type at all for a .jpg; the extension is
      // only a hint here — the server decides from the bytes.
      if (!/^image\/(png|jpeg)$/.test(file.type) && !/\.(png|jpe?g)$/.test(name)) {
        showError('A logo needs to be a PNG or JPEG image.');
        return false;
      }
      if (file.size > maxBytes) {
        showError('That file is too large. A logo can be up to '
          + Math.round(maxBytes / 1048576) + ' MB.');
        return false;
      }
      return true;
    }

    function send() {
      showError('');
      form.classList.add('is-uploading');
      form.submit();
    }

    input.addEventListener('change', function () {
      var file = input.files[0];
      if (!file) return;
      if (acceptable(file)) send();
      else input.value = '';
    });

    if (!form.hasAttribute('data-logo-drop')) return;
    ['dragenter', 'dragover'].forEach(function (type) {
      form.addEventListener(type, function (event) {
        event.preventDefault();
        form.classList.add('is-dragging');
      });
    });
    ['dragleave', 'drop'].forEach(function (type) {
      form.addEventListener(type, function () { form.classList.remove('is-dragging'); });
    });
    form.addEventListener('drop', function (event) {
      event.preventDefault();
      var files = event.dataTransfer.files;
      if (!files.length) return;
      if (files.length > 1) {
        showError('Drop one image at a time.');
        return;
      }
      if (!acceptable(files[0])) return;
      input.files = files;
      send();
    });
  });

  // --- Layout cards -------------------------------------------------------

  var colourSection = root.querySelector('[data-colour-section]');
  var layoutInputs = root.querySelectorAll('input[name=invoice_template]');

  // The colour only means something to Banded, so it's shown only then.
  // Hidden rather than disabled: the stored colour is still submitted and
  // kept, ready for when Banded is chosen again.
  function syncLayout() {
    var chosen = root.querySelector('input[name=invoice_template]:checked');
    colourSection.hidden = !chosen || chosen.value !== 'banded';
  }
  layoutInputs.forEach(function (input) { input.addEventListener('change', syncLayout); });
  syncLayout();

  // --- Footer -------------------------------------------------------------

  var footerToggle = root.querySelector('[data-footer-toggle]');
  var footerFields = root.querySelector('[data-footer-fields]');

  function syncFooter() {
    footerFields.hidden = !footerToggle.checked;
    root.classList.toggle('has-footer', footerToggle.checked);
  }
  footerToggle.addEventListener('change', syncFooter);
  syncFooter();

  // --- Colour pickers -----------------------------------------------------
  //
  // One per [data-color-picker]: the accent colour and the footer's two.
  // Each keeps its own section variable (data-css-var) in step, and the
  // accent one also the readable text colour on top of it.

  var pickers = [];

  function setUpPicker(picker) {
    var field = picker.querySelector('[data-color-hex]');
    var swatch = picker.querySelector('[data-color-toggle]');
    var panel = picker.querySelector('[data-color-panel]');
    var area = picker.querySelector('[data-color-area]');
    var areaHandle = area.querySelector('span');
    var hueBar = picker.querySelector('[data-color-hue]');
    var hueHandle = hueBar.querySelector('span');
    var cssVar = picker.dataset.cssVar;
    var contrastVar = picker.dataset.contrastVar;

    var current = HEX.test(field.value.toLowerCase()) ? field.value.toLowerCase() : '#1c1a17';
    var state = rgbToHsv(hexToRgb(current));

    function paint(keepFieldText) {
      if (!keepFieldText) field.value = current;
      swatch.style.background = current;
      if (cssVar) root.style.setProperty(cssVar, current);
      if (contrastVar) root.style.setProperty(contrastVar, textOn(current));
      picker.style.setProperty('--picker-hue', String(Math.round(state.h)));
      areaHandle.style.left = (state.s * 100) + '%';
      areaHandle.style.top = ((1 - state.v) * 100) + '%';
      areaHandle.style.background = current;
      hueHandle.style.left = (state.h / 360 * 100) + '%';
      area.setAttribute('aria-valuetext', 'Saturation ' + Math.round(state.s * 100)
        + '%, brightness ' + Math.round(state.v * 100) + '%');
      hueBar.setAttribute('aria-valuenow', String(Math.round(state.h)));
    }

    // Set from an exact hex (the text field): that hex is what's kept,
    // rather than a round trip through HSV that could shift it a unit.
    function setHex(hex, keepFieldText) {
      current = hex;
      state = rgbToHsv(hexToRgb(hex), state.h);
      paint(keepFieldText);
    }

    // Set from the area or the hue bar.
    function setFromState() {
      current = rgbToHex(hsvToRgb(state.h % 360, state.s, state.v));
      paint(false);
    }

    function open(isOpen) {
      panel.hidden = !isOpen;
      swatch.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    }

    swatch.addEventListener('click', function () {
      var opening = panel.hidden;
      // One open at a time.
      pickers.forEach(function (other) { other.close(); });
      open(opening);
      if (opening) area.focus();
    });
    picker.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && !panel.hidden) {
        open(false);
        swatch.focus();
      }
    });

    field.addEventListener('input', function () {
      var value = field.value.trim().toLowerCase();
      if (value && value[0] !== '#') value = '#' + value;
      if (HEX.test(value)) setHex(value, true);
    });
    field.addEventListener('blur', function () { field.value = current; });

    dragOn(area, function (event) {
      var box = area.getBoundingClientRect();
      state.s = clamp((event.clientX - box.left) / box.width);
      state.v = 1 - clamp((event.clientY - box.top) / box.height);
      setFromState();
    });
    dragOn(hueBar, function (event) {
      var box = hueBar.getBoundingClientRect();
      state.h = clamp((event.clientX - box.left) / box.width) * 359.9;
      setFromState();
    });

    area.addEventListener('keydown', function (event) {
      var step = event.shiftKey ? 0.1 : 0.01;
      var moves = {
        ArrowLeft: ['s', -step], ArrowRight: ['s', step],
        ArrowUp: ['v', step], ArrowDown: ['v', -step],
      };
      var move = moves[event.key];
      if (!move) return;
      event.preventDefault();
      state[move[0]] = clamp(state[move[0]] + move[1]);
      setFromState();
    });
    hueBar.addEventListener('keydown', function (event) {
      var step = event.shiftKey ? 10 : 1;
      var delta = { ArrowLeft: -step, ArrowDown: -step, ArrowRight: step, ArrowUp: step }[event.key];
      if (event.key === 'Home') delta = -state.h;
      if (event.key === 'End') delta = 359.9 - state.h;
      if (delta === undefined) return;
      event.preventDefault();
      state.h = Math.min(359.9, Math.max(0, state.h + delta));
      setFromState();
    });

    paint(false);
    return {
      element: picker,
      close: function () { if (!panel.hidden) open(false); },
    };
  }

  function dragOn(element, update) {
    element.addEventListener('pointerdown', function (event) {
      event.preventDefault();
      element.focus();
      element.setPointerCapture(event.pointerId);
      update(event);
      function move(e) { update(e); }
      function stop() {
        element.removeEventListener('pointermove', move);
        element.removeEventListener('pointerup', stop);
        element.removeEventListener('pointercancel', stop);
      }
      element.addEventListener('pointermove', move);
      element.addEventListener('pointerup', stop);
      element.addEventListener('pointercancel', stop);
    });
  }

  root.querySelectorAll('[data-color-picker]').forEach(function (picker) {
    pickers.push(setUpPicker(picker));
  });

  // Closes like a menu: a click anywhere outside the open picker. Not on
  // another picker's swatch, though — that swatch closes the others itself,
  // on click. Closing here, on pointerdown, would reflow the row (an open
  // panel is wider than its swatch) and move the swatch out from under the
  // pointer before the click arrived.
  document.addEventListener('pointerdown', function (event) {
    if (event.target.closest('[data-color-toggle]')) return;
    pickers.forEach(function (picker) {
      if (!picker.element.contains(event.target)) picker.close();
    });
  });
})();
