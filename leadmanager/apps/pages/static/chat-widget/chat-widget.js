/**
 * Estrasol Chat Widget — chatbot embebible.
 *
 * Uso (un solo tag):
 *   <script src="https://TU_DOMINIO/static/chat-widget/chat-widget.js"
 *           data-chat-url="https://TU_DOMINIO/chat/"
 *           data-position="right"
 *           async></script>
 *
 * Atributos opcionales:
 *   - data-chat-url:   URL de la página del chat (por defecto se deriva del origen del script + '/chat/').
 *   - data-avatar-url: imagen personalizada de la burbuja (por defecto, la del agente activo de chatweb).
 *   - data-position:   'right' (por defecto) o 'left'.
 *   - data-title:      texto del tooltip de la burbuja.
 *   - data-unit:       unidad de negocio de la página donde va el tag. Con él, el chat se
 *                      salta la pregunta inicial y abre pidiendo nombre y correo. Un valor
 *                      que no corresponda a ninguna unidad se ignora y se pregunta igual
 *                      que siempre.
 *
 *                      Sirve la etiqueta completa o cualquiera de sus tramos separados
 *                      por "/", sin distinguir mayúsculas:
 *
 *                        Sitio web/e-commerce ....... "sitio web"  o  "e-commerce"
 *                        Kioscos digitales .......... "kioscos digitales"
 *                        Inteligencia Artificial .... "inteligencia artificial"
 *                        Smartketing/Más clientes ... "smartketing"  o  "más clientes"
 *                        Odoo/ERP y CRM ............. "odoo"  o  "erp y crm"
 *                        Ciberseguridad ............. "ciberseguridad"
 *
 *                      En una página que no sea de una unidad (home, blog, contacto) se
 *                      omite el atributo. La lista buena es ``BUSINESS_UNITS``, en la
 *                      página del chat: si cambia ahí, cambian los valores aceptados aquí.
 *
 * Un solo tag por página. Si el sitio ya inyecta el widget de forma global, un
 * segundo tag se ignora por completo, y con él su ``data-unit``, sin avisar de
 * nada. En las páginas de unidad hay que reemplazar el tag global, no sumarle otro.
 *
 * Aquí solo viven la burbuja y el panel. Todo el flujo (unidad de negocio,
 * nombre, correo y la conversación) lo lleva la página del chat, dentro del
 * iframe, con su propio diseño.
 *
 * La burbuja y el panel se montan dentro de un Shadow DOM, para que el CSS
 * del sitio anfitrión no los alcance. Cuando vivían en el DOM normal, el
 * tema de WordPress les aplicaba sus estilos de ``button`` e ``input``:
 * apilaba los botones a todo lo ancho y deformaba la burbuja.
 */
(function () {
    'use strict';

    // localhost y 127.0.0.1 son para probarlo en desarrollo con
    // /widget-demo/; ningún sitio real se sirve con esos nombres.
    var dominiosPermitidos = [
        'estrasol.com.mx', 'www.estrasol.com.mx', 'chat.estrasol.com.mx',
        'localhost', '127.0.0.1'
    ];
    if (dominiosPermitidos.indexOf(window.location.hostname) === -1) {
        console.warn('Widget no autorizado para este dominio.');
        return;
    }

    if (window.__EstrasolChatWidgetLoaded) return;
    window.__EstrasolChatWidgetLoaded = true;

    var currentScript = document.currentScript || document.querySelector('script[data-chat-url]') || (function () {
        var scripts = document.getElementsByTagName('script');
        return scripts[scripts.length - 1];
    })();

    function originOf(url) {
        var a = document.createElement('a');
        a.href = url;
        return a.origin || (a.protocol + '//' + a.host);
    }

    function readConfig() {
        var base = currentScript ? (currentScript.src || '') : '';
        var origin = base ? originOf(base) : window.location.origin;

        return {
            chatUrl: (currentScript && currentScript.getAttribute('data-chat-url')) || origin + '/chat/',
            imgUrl: (currentScript && currentScript.getAttribute('data-avatar-url')) || origin + '/widget-avatar/',
            position: ((currentScript && currentScript.getAttribute('data-position')) || 'right').toLowerCase() === 'left' ? 'left' : 'right',
            title: (currentScript && currentScript.getAttribute('data-title')) || '¿Cómo podemos ayudarte?',
            unit: ((currentScript && currentScript.getAttribute('data-unit')) || '').trim()
        };
    }

    var config = readConfig();
    var chatOrigin = originOf(config.chatUrl);
    var side = config.position;

    // De dónde llegó el visitante. Los pone el sitio en el tag:
    //
    //   <script src=".../chat-widget.js"
    //           data-utm-source="google"
    //           data-utm-medium="cpc"
    //           data-utm-campaign="verano2026"
    //           data-utm-content="anuncio-a"
    //           data-utm-term="kioscos+digitales"></script>
    //
    // Hace falta este puente porque el chat vive en un iframe: dentro de
    // él, window.location es la URL del propio chat, no la de la página.
    // Las UTM de la página solo las ve este script, que sí corre ahí.
    //
    // Se leen tarde, no al cargar: el iframe se crea la primera vez que
    // se abre el panel, así que un sitio que ponga los atributos con
    // JavaScript después de cargar también funciona.
    var UTM_KEYS = ['source', 'medium', 'campaign', 'content', 'term'];

    function readUtms() {
        var utms = {};

        UTM_KEYS.forEach(function (clave) {
            var valor = currentScript
                && currentScript.getAttribute('data-utm-' + clave);

            // Se recorta al tope de la columna: no tiene caso mandar más
            // de lo que se va a guardar.
            utms['utm_' + clave] = (valor || '').trim().slice(0, 255);
        });

        utms['traffic_origin'] = ((currentScript && currentScript.getAttribute('data-traffic-origin')) || '').trim().slice(0, 255);

        return utms;
    }

    // La unidad viaja a la página del chat por la URL, que es como ya
    // recibe el resto de los datos. En una página de Odoo el tag lleva
    // data-unit="odoo" y la conversación abre directo en el paso
    // siguiente, sin volver a preguntar algo que la página ya dice.
    // Quien valida el valor es el chat, que es donde vive la lista: un
    // valor que no corresponde a ninguna unidad se ignora y se pregunta
    // como siempre.
    function chatUrlWithParams() {
        var url = config.chatUrl;

        function append(clave, valor) {
            if (!valor) return;
            var separator = url.indexOf('?') === -1 ? '?' : '&';
            url += separator + clave + '=' + encodeURIComponent(valor);
        }

        append('unit', config.unit);

        var utms = readUtms();
        Object.keys(utms).forEach(function (clave) {
            append(clave, utms[clave]);
        });

        return url;
    }

    // Pantalla angosta, o baja como un teléfono acostado (~390 px de alto):
    // ahí el panel flotante quedaba de unos 250 px y no se podía usar, así
    // que ocupa toda la pantalla.
    var FULLSCREEN_QUERY = '(max-width: 640px), (max-height: 520px)';

    // ---------- ESTILOS ----------
    // Viven dentro del shadow root: ni se escapan al sitio ni el sitio los
    // pisa. Los prefijos ``ew-`` solo importan en el respaldo sin Shadow DOM.
    var css = [
        ':host { all: initial; }',
        '[hidden] { display: none !important; }',

        // Burbuja flotante
        '.ew-bubble {',
        '  position: fixed; z-index: 2147483001;',
        '  bottom: 24px; ' + side + ': 24px;',
        '  width: 80px; height: 80px; margin: 0; padding: 0;',
        '  border: none; border-radius: 50%; background: transparent; cursor: pointer;',
        '  display: flex; align-items: center; justify-content: center;',
        '  -webkit-tap-highlight-color: transparent;',
        '  transition: transform 0.25s cubic-bezier(0.23, 1, 0.32, 1);',
        '}',
        '.ew-bubble:hover { transform: translateY(-4px) scale(1.04); }',
        '.ew-bubble:active { transform: scale(0.95); }',
        '.ew-bubble:focus-visible { outline: 3px solid rgba(36, 81, 245, 0.6); outline-offset: 3px; }',
        '.ew-avatar { width: 100%; height: 100%; border-radius: 50%; overflow: hidden; display: block; }',
        '.ew-avatar img { width: 100%; height: 100%; object-fit: cover; display: block; }',
        '.ew-badge {',
        '  position: absolute; top: -2px; right: -2px; box-sizing: border-box;',
        '  width: 20px; height: 20px; border-radius: 50%;',
        '  background: #5524ef; color: #fff;',
        '  font: 700 11px/1 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;',
        '  display: flex; align-items: center; justify-content: center;',
        '  border: 2px solid #fff; box-shadow: 0 2px 6px rgba(0, 2, 17, 0.2);',
        '}',
        '@keyframes ew-pulse { 0% { box-shadow: 0 0 0 0 rgba(85, 36, 239, 0.55); } 70% { box-shadow: 0 0 0 18px rgba(85, 36, 239, 0); } 100% { box-shadow: 0 0 0 0 rgba(85, 36, 239, 0); } }',
        '.ew-bubble.ew-pulse { animation: ew-pulse 2.4s infinite; }',

        // Panel con el iframe del chat. Cerrado queda con visibility:hidden
        // (con retraso, para que se vea el fade): sin eso el iframe
        // invisible seguía recibiendo el foco con Tab.
        '.ew-panel {',
        '  position: fixed; z-index: 2147483002; box-sizing: border-box;',
        '  bottom: 112px; ' + side + ': 24px;',
        '  width: 380px; max-width: calc(100vw - 32px);',
        '  height: 620px; max-height: calc(100vh - 124px);',
        '  background: #f4f7ff; border-radius: 22px; overflow: hidden;',
        '  border: 1px solid rgba(255, 255, 255, 0.7);',
        '  box-shadow: 0 30px 80px rgba(0, 2, 17, 0.35);',
        '  opacity: 0; transform: translateY(20px) scale(0.96);',
        '  pointer-events: none; visibility: hidden;',
        '  transition: opacity 0.28s cubic-bezier(0.23, 1, 0.32, 1), transform 0.28s cubic-bezier(0.23, 1, 0.32, 1), visibility 0s linear 0.28s;',
        '}',
        '.ew-panel.ew-open {',
        '  opacity: 1; transform: none; pointer-events: auto; visibility: visible;',
        '  transition: opacity 0.28s cubic-bezier(0.23, 1, 0.32, 1), transform 0.28s cubic-bezier(0.23, 1, 0.32, 1), visibility 0s;',
        '}',
        '.ew-panel iframe { width: 100%; height: 100%; border: 0; display: block; outline: none; }',

        // Mientras carga el iframe la primera vez
        '.ew-loader { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; }',
        '.ew-loader span {',
        '  box-sizing: border-box; width: 34px; height: 34px; border-radius: 50%;',
        '  border: 3px solid #d9dfff; border-top-color: #2451f5;',
        '  animation: ew-spin 0.8s linear infinite;',
        '}',
        '@keyframes ew-spin { to { transform: rotate(360deg); } }',

        // Pantalla completa. Con top/right/bottom/left en 0 el panel sigue
        // al viewport visible también cuando el navegador del teléfono
        // esconde o muestra sus barras, cosa que 100vh no hace.
        '@media ' + FULLSCREEN_QUERY + ' {',
        '  .ew-panel {',
        '    top: 0 !important; right: 0 !important; bottom: 0 !important; left: 0 !important;',
        '    width: auto !important; height: auto !important;',
        '    max-width: none !important; max-height: none !important;',
        '    border-radius: 0; border: 0;',
        '  }',
        '  .ew-bubble { bottom: 18px; ' + side + ': 18px; width: 68px; height: 68px; }',
        '}'
    ].join('\n');

    function buildWidget() {
        // Etiqueta propia en vez de un <div>: los temas estilan ``div``
        // a lo bruto, casi nunca etiquetas que no conocen. El estilo en
        // línea le gana a cualquier regla normal del sitio.
        var host = document.createElement('estrasol-chat');
        host.style.cssText = 'all: initial;';
        document.body.appendChild(host);

        // Sin Shadow DOM (navegadores muy viejos) todo cae en el DOM normal:
        // se ve igual, solo que ya sin el aislamiento.
        var root = host.attachShadow ? host.attachShadow({ mode: 'open' }) : host;

        var style = document.createElement('style');
        style.textContent = css;
        (root === host ? document.head : root).appendChild(style);

        // Burbuja
        var bubble = document.createElement('button');
        bubble.type = 'button';
        bubble.className = 'ew-bubble ew-pulse';
        bubble.setAttribute('aria-label', 'Abrir chat');
        bubble.title = config.title;
        bubble.innerHTML = '<span class="ew-avatar"><img alt=""></span>' +
            '<span class="ew-badge">1</span>';
        bubble.querySelector('img').src = config.imgUrl;

        // Panel. El iframe se crea la primera vez que se abre: quien nunca
        // abre el chat no carga la página ni hace ninguna petición.
        var panel = document.createElement('div');
        panel.className = 'ew-panel';
        panel.setAttribute('role', 'dialog');
        panel.setAttribute('aria-label', 'Chat con el asistente');

        var loader = document.createElement('div');
        loader.className = 'ew-loader';
        loader.innerHTML = '<span></span>';
        panel.appendChild(loader);

        root.appendChild(bubble);
        root.appendChild(panel);

        var iframe = null;
        var open = false;

        function ensureIframe() {
            if (iframe) return;

            iframe = document.createElement('iframe');
            iframe.setAttribute('title', 'Chat Estrasol');
            iframe.setAttribute('allow', 'microphone');
            iframe.addEventListener('load', function () {
                loader.hidden = true;
            });
            iframe.setAttribute('src', chatUrlWithParams());
            panel.appendChild(iframe);
        }

        function isFullscreen() {
            return !!(window.matchMedia && window.matchMedia(FULLSCREEN_QUERY).matches);
        }

        // Pone el panel justo encima de la burbuja, según su tamaño real y
        // el espacio disponible. En pantalla completa no aplica: ahí manda
        // el CSS.
        function positionPanel() {
            if (isFullscreen()) {
                panel.style.bottom = '';
                panel.style.maxHeight = '';
                return;
            }

            var bubbleRect = bubble.getBoundingClientRect();
            var gap = 16;
            var fromBottom = window.innerHeight - bubbleRect.bottom;
            var bottom = fromBottom + bubbleRect.height + gap;
            panel.style.bottom = bottom + 'px';
            panel.style.maxHeight = 'calc(100dvh - ' + (bottom + 24) + 'px)';
        }

        function toggle() {
            open = !open;

            if (open) ensureIframe();

            panel.classList.toggle('ew-open', open);
            bubble.classList.remove('ew-pulse');
            bubble.setAttribute('aria-label', open ? 'Cerrar chat' : 'Abrir chat');
            bubble.querySelector('.ew-badge').hidden = open;

            if (open) {
                positionPanel();
                // La primera vez el iframe aún no carga y el mensaje se
                // perdería; ahí el chat se enfoca solo al terminar de cargar.
                if (loader.hidden && iframe.contentWindow) {
                    iframe.contentWindow.postMessage({ type: 'estrasol-chat-focus' }, chatOrigin);
                }
            }
        }

        // El panel se abre únicamente cuando el visitante pulsa la burbuja.
        bubble.addEventListener('click', toggle);

        window.addEventListener('resize', function () {
            if (open) positionPanel();
        });

        window.addEventListener('message', function (e) {
            // Solo el iframe del chat puede hablar con el widget.
            if (!iframe || e.source !== iframe.contentWindow) return;
            if (e.origin !== chatOrigin) return;
            if (!e.data || typeof e.data !== 'object') return;

            if (e.data.type === 'estrasol-chat-close' && open) {
                toggle();
                bubble.focus();
            }

            if (e.data.type === 'sofia-lead-generado') {
                var key = 'sofia-lead-' + (e.data.leadId || 'x');
                try {
                    if (window.sessionStorage.getItem(key)) return;
                    window.sessionStorage.setItem(key, '1');
                } catch (err) {}
                window.dataLayer = window.dataLayer || [];
                window.dataLayer.push({
                    event: 'sofia_lead_generado',
                    lead_type: 'chatbot_ia',
                    chat_unit: config.unit || 'sin_unidad'
                });
            }
        });
    }

    function init() {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', buildWidget);
        } else {
            buildWidget();
        }
    }

    init();
})();
