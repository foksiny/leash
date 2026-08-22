/* Leash docs - shared behaviour */
(function () {
    'use strict';

    /* --- sidebar: highlight current page --- */
    var here = location.href.split('#')[0];
    document.querySelectorAll('.sidebar a').forEach(function (link) {
        if (link.href === here) link.classList.add('active');
    });

    /* --- mobile menu: tap outside closes it --- */
    var sidebar = document.querySelector('.sidebar');
    if (sidebar) {
        document.addEventListener('click', function (e) {
            if (!sidebar.classList.contains('open')) return;
            if (sidebar.contains(e.target)) return;
            var t = document.querySelector('.menu-toggle');
            if (t && t.contains(e.target)) return;
            sidebar.classList.remove('open');
        });
    }

    /* --- heading anchors + ids --- */
    var used = {};
    document.querySelectorAll('h2, h3').forEach(function (h) {
        var id = h.id || (h.textContent || '')
            .toLowerCase().replace(/<[^>]*>/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
        if (!id || used[id]) { var n = 2; while (used[id + '-' + n]) n++; id = id ? id + '-' + n : 'section-' + n; }
        used[id] = true;
        h.id = id;
        var a = document.createElement('a');
        a.className = 'anchor-link';
        a.href = '#' + id;
        a.textContent = '#';
        a.setAttribute('aria-label', 'Link to this section');
        h.appendChild(a);
    });

    /* --- copy buttons on code blocks --- */
    document.querySelectorAll('pre').forEach(function (pre) {
        var btn = document.createElement('button');
        btn.className = 'copy-btn';
        btn.type = 'button';
        btn.textContent = 'COPY';
        btn.addEventListener('click', function () {
            var text = pre.innerText.replace(/\n$/, '');
            function done(ok) {
                btn.textContent = ok ? 'COPIED' : 'FAILED';
                btn.classList.add('copied');
                setTimeout(function () {
                    btn.textContent = 'COPY';
                    btn.classList.remove('copied');
                }, 1200);
            }
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(function () { done(true); },
                                                         function () { fallback(); });
            } else {
                fallback();
            }
            function fallback() {
                try {
                    var ta = document.createElement('textarea');
                    ta.value = text;
                    ta.style.position = 'fixed';
                    ta.style.opacity = '0';
                    document.body.appendChild(ta);
                    ta.select();
                    var ok = document.execCommand('copy');
                    document.body.removeChild(ta);
                    done(ok);
                } catch (err) { done(false); }
            }
        });
        pre.appendChild(btn);
    });

    /* --- responsive table wrappers --- */
    document.querySelectorAll('table').forEach(function (table) {
        if (table.parentElement && table.parentElement.classList.contains('table-wrap')) return;
        var wrap = document.createElement('div');
        wrap.className = 'table-wrap';
        table.parentNode.insertBefore(wrap, table);
        wrap.appendChild(table);
    });

    /* --- footer --- */
    var main = document.querySelector('.main');
    if (main) {
        var foot = document.createElement('div');
        foot.className = 'page-footer';
        foot.innerHTML = 'Part of the <a href="index.html">Leash documentation</a>' +
            ' &middot; <a href="https://github.com/anomalyco/opencode/issues">Report an issue</a>';
        main.appendChild(foot);
    }

    /* --- back to top --- */
    var top = document.createElement('button');
    top.className = 'back-to-top';
    top.type = 'button';
    top.title = 'Back to top';
    top.setAttribute('aria-label', 'Back to top');
    top.innerHTML = '&#8593;';
    top.addEventListener('click', function () {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });
    window.addEventListener('scroll', function () {
        top.classList.toggle('visible', window.scrollY > 400);
    }, { passive: true });
    document.body.appendChild(top);
})();
