/* Leash documentation — shared behaviour, version 2.3.0
 * - theme toggle (persisted in localStorage)
 * - client-side search over a compact topic index
 * - reading progress, heading anchors, copy buttons, mobile nav
 */
(function () {
    'use strict';

    /* ---------- theme ---------- */
    var root = document.documentElement;
    try {
        var saved = localStorage.getItem('leash-docs-theme');
        if (saved === 'light' || saved === 'dark') root.dataset.theme = saved;
    } catch (e) { /* storage disabled: keep the default */ }
    var themeBtn = document.querySelector('.theme-toggle');
    if (themeBtn) {
        themeBtn.addEventListener('click', function () {
            var next = root.dataset.theme === 'light' ? 'dark' : 'light';
            root.dataset.theme = next;
            try { localStorage.setItem('leash-docs-theme', next); } catch (e) {}
        });
    }

    /* ---------- current page in sidebar ---------- */
    var here = location.href.split('#')[0].split('/').pop();
    document.querySelectorAll('.sidenav a').forEach(function (link) {
        if (link.getAttribute('href') === here) link.classList.add('active');
    });

    /* ---------- mobile menu ---------- */
    var sidebar = document.querySelector('.sidenav');
    var menuToggle = document.querySelector('.menu-toggle');
    if (menuToggle) {
        menuToggle.addEventListener('click', function () {
            if (sidebar) sidebar.classList.toggle('open');
            menuToggle.setAttribute('aria-expanded', sidebar && sidebar.classList.contains('open') ? 'true' : 'false');
        });
    }
    if (sidebar) {
        document.addEventListener('click', function (e) {
            if (!sidebar.classList.contains('open')) return;
            if (sidebar.contains(e.target) || (menuToggle && menuToggle.contains(e.target))) return;
            sidebar.classList.remove('open');
        });
    }

    /* ---------- heading ids, anchors, TOC ---------- */
    var used = {};
    document.querySelectorAll('.content h2, .content h3').forEach(function (h) {
        var id = h.id || (h.textContent || '')
            .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
        if (!id || used[id]) {
            var n = 2;
            while (used[id + '-' + n]) n++;
            id = id ? id + '-' + n : 'section-' + n;
        }
        used[id] = true;
        h.id = id;
        var a = document.createElement('a');
        a.className = 'anchor-link';
        a.href = '#' + id;
        a.textContent = '#';
        a.setAttribute('aria-label', 'Link to this section');
        h.appendChild(a);
    });
    var toc = document.querySelector('.docnav');
    if (toc) {
        toc.querySelectorAll('a').forEach(function (a) {
            var target = document.getElementById(a.hash.slice(1));
            if (!target) a.remove();
        });
    }

    /* ---------- reading progress ---------- */
    var bar = document.getElementById('progress-bar');
    function updateProgress() {
        if (!bar) return;
        var max = document.documentElement.scrollHeight - window.innerHeight;
        bar.style.width = (max > 0 ? Math.min(100, (window.scrollY / max) * 100) : 0) + '%';
    }
    window.addEventListener('scroll', updateProgress, { passive: true });
    window.addEventListener('resize', updateProgress);
    updateProgress();

    /* ---------- copy buttons ---------- */
    document.querySelectorAll('pre').forEach(function (pre) {
        if (pre.querySelector('.copy-btn')) return;
        var btn = document.createElement('button');
        btn.className = 'copy-btn';
        btn.type = 'button';
        btn.textContent = 'COPY';
        btn.addEventListener('click', function () {
            var text = pre.innerText.replace(/\n$/, '').replace(/COPY$/, '').trimEnd();
            function done(ok) {
                btn.textContent = ok ? 'COPIED' : 'FAILED';
                btn.classList.toggle('copied', ok);
                setTimeout(function () { btn.textContent = 'COPY'; btn.classList.remove('copied'); }, 1300);
            }
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(function () { done(true); }, fallback);
            } else { fallback(); }
            function fallback() {
                try {
                    var ta = document.createElement('textarea');
                    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
                    document.body.appendChild(ta); ta.select();
                    done(document.execCommand('copy'));
                    document.body.removeChild(ta);
                } catch (err) { done(false); }
            }
        });
        pre.appendChild(btn);
    });

    /* ---------- responsive table wrappers ---------- */
    document.querySelectorAll('table').forEach(function (table) {
        if (table.parentElement && table.parentElement.classList.contains('table-wrap')) return;
        var wrap = document.createElement('div');
        wrap.className = 'table-wrap';
        table.parentNode.insertBefore(wrap, table);
        wrap.appendChild(table);
    });

    /* ---------- footer ---------- */
    var main = document.querySelector('.content');
    if (main) {
        var foot = document.createElement('div');
        foot.className = 'page-footer';
        foot.innerHTML = 'Leash 2.3.0 documentation &middot; <a href="index.html">Overview</a>' +
            ' &middot; <a href="https://github.com/foksiny/leash">Source</a>';
        main.appendChild(foot);
    }

    /* ---------- back to top ---------- */
    var top = document.createElement('button');
    top.className = 'back-to-top';
    top.type = 'button';
    top.title = 'Back to top';
    top.setAttribute('aria-label', 'Back to top');
    top.innerHTML = '&#8593;';
    top.addEventListener('click', function () { window.scrollTo({ top: 0, behavior: 'smooth' }); });
    window.addEventListener('scroll', function () {
        top.classList.toggle('visible', window.scrollY > 420);
    }, { passive: true });
    document.body.appendChild(top);

    /* ---------- search ---------- */
    var INDEX = [
        ['Overview', 'index.html', 'features, quick links, what is Leash'],
        ['Installation & Setup', 'getting-started.html', 'prerequisites, install, first program, projects'],
        ['Language Reference', 'language-guide.html', 'types, variables, functions, strings, structs, classes, generics, operators, control flow, semicolons'],
        ['Compiler & CLI', 'compiler.html', 'compile, run, check, dump, optimization levels, targets, arena, gc'],
        ['Self-Hosted Compiler', 'self-hosted.html', 'bootstrap, stages, fixed point, parity corpus'],
        ['Standard Library', 'stdlib.html', 'modules, collections, math, text, net, sql, utils'],
        ['Package Manager', 'package-manager.html', 'pkg init, publish, install, lockfile, registry, semver'],
        ['Concurrency Model', 'concurrency.html', 'workers, spawn, shared, fusion, async, await, futures'],
        ['Advanced Topics', 'advanced.html', 'ffi, memory, arena, escapes, root arena, errors, unsafe, getstruct'],
        ['Known Limitations', 'limitations.html', 'arena, jvm sql, serve_tls, http3'],
        ['Strings start and end', 'language-guide.html#string-methods', 'start(), end(), substring, utf8'],
        ['Console input', 'language-guide.html#input-handling', 'get, getint, getfloat, getstruct, keyget'],
        ['Optional semicolons', 'language-guide.html#optional-semicolons', 'statement terminators, syntax'],
        ['Memory management modes', 'compiler.html#memory-management-modes', 'arena default, --gc, autofree, nogc'],
        ['Lifetime audit', 'advanced.html#lifetime-audit', 'E_ARENA_LIFETIME, provenance, compile-or-fail, root'],
        ['Reading lifetime diagnostics', 'advanced.html#reading-lifetime-diagnostics', 'why, help, trace, error code, source snippet'],
        ['Escapes and root arena', 'advanced.html#arena-escapes', 'escaping &x, pointee, snapshot, staging, worker'],
        ['Runtime environment variables', 'advanced.html#arena-env', 'LEASH_ARENA_POISON, VERIFY, ESCAPE_SNAPSHOT, STATS'],
        ['Editor support', 'getting-started.html#editor-support', 'vscode, lsp, diagnostics, completion, hover, npm test'],
        ['Cross-thread roots', 'concurrency.html', 'spawn, worker pointee, container, class, shared registry'],
        ['New in 2.3.0', 'index.html#whats-new', 'first-person voxel game, OpenGL window, C ABI struct/float fixes']
    ];
    var input = document.getElementById('doc-search');
    if (!input) return;
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', 'doc-search-results');
    var box = null;
    var active = -1;
    function close() {
        if (box) { box.remove(); box = null; }
        active = -1;
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
    }
    function resultLinks() { return box ? box.querySelectorAll('a') : []; }
    function setActive(i) {
        var links = resultLinks();
        if (!links.length) return;
        active = (i + links.length) % links.length;
        for (var k = 0; k < links.length; k++) links[k].classList.toggle('active', k === active);
        input.setAttribute('aria-activedescendant', links[active].id);
        links[active].scrollIntoView({ block: 'nearest' });
    }
    function search(q) {
        close();
        if (!q || q.trim().length < 2) return;
        q = q.trim().toLowerCase();
        var hits = INDEX.filter(function (e) {
            return (e[0] + ' ' + e[2]).toLowerCase().indexOf(q) !== -1;
        }).slice(0, 8);
        box = document.createElement('div');
        box.className = 'search-results';
        box.id = 'doc-search-results';
        box.setAttribute('role', 'listbox');
        input.setAttribute('aria-expanded', 'true');
        if (!hits.length) {
            var empty = document.createElement('div');
            empty.className = 'search-empty';
            empty.setAttribute('role', 'status');
            empty.setAttribute('aria-live', 'polite');
            empty.textContent = 'No matches for “' + q.trim() + '”';
            box.appendChild(empty);
        } else {
            hits.forEach(function (e) {
                var a = document.createElement('a');
                a.href = e[1];
                a.id = 'doc-search-option-' + hits.indexOf(e);
                a.setAttribute('role', 'option');
                var title = document.createElement('span');
                title.textContent = e[0];
                var sub = document.createElement('small');
                sub.textContent = e[2];
                a.appendChild(title);
                a.appendChild(sub);
                box.appendChild(a);
            });
        }
        document.body.appendChild(box);
    }
    input.addEventListener('input', function () { search(input.value); });
    input.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { close(); input.blur(); return; }
        var links = resultLinks();
        if (!links.length) return;
        if (e.key === 'ArrowDown') { e.preventDefault(); setActive(active + 1); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(active - 1); }
        else if (e.key === 'Enter' && active >= 0) { e.preventDefault(); links[active].click(); }
    });
    document.addEventListener('click', function (e) {
        if (box && !box.contains(e.target) && e.target !== input) close();
    });
})();
