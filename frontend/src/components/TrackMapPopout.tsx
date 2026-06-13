import { useEffect, useRef, useCallback } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { useTelemetryStore } from '../store/telemetryStore';
import { TrackMap } from './TrackMap';
import { TrackMap3D } from './TrackMap3D';

const plog = (...args: any[]) => {
    console.log('[popout]', ...args);
    if (import.meta.env.DEV) {
        const w = window as any;
        (w.__popoutLogs = w.__popoutLogs || []).push(args.map(a => String(a)).join(' '));
    }
};

function PopoutContent() {
    const show3D = useTelemetryStore(s => s.show3DLab);

    return (
        <div style={{ width: '100vw', height: '100vh', background: '#0a0a0e' }}>
            {show3D ? (
                <TrackMap3D isAnimating={false} isPopout={true} />
            ) : (
                <TrackMap isExpanded={true} isAnimating={false} isPopout={true} />
            )}
        </div>
    );
}

export function TrackMapPopout() {
    const isOpen = useTelemetryStore(s => s.isTrackMapPoppedOut);
    const setOpen = useTelemetryStore(s => s.setTrackMapPoppedOut);
    const windowRef = useRef<Window | null>(null);
    const rootRef = useRef<Root | null>(null);
    const observerRef = useRef<MutationObserver | null>(null);

    const cleanup = useCallback(() => {
        if (rootRef.current) plog('unmounting react root');
        observerRef.current?.disconnect();
        observerRef.current = null;
        rootRef.current?.unmount();
        rootRef.current = null;
    }, []);

    useEffect(() => {
        if (!isOpen) {
            cleanup();
            if (windowRef.current && !windowRef.current.closed) {
                windowRef.current.close();
            }
            windowRef.current = null;
            return;
        }

        if (windowRef.current && !windowRef.current.closed) {
            windowRef.current.focus();
            return;
        }

        const w = Math.round(screen.width * 0.6);
        const h = Math.round(screen.height * 0.7);
        const left = Math.round((screen.width - w) / 2);
        const top = Math.round((screen.height - h) / 2);

        const popup = window.open(
            '', 'lmu-trackmap',
            `width=${w},height=${h},left=${left},top=${top},resizable=yes`
        );
        if (!popup) {
            setOpen(false);
            return;
        }

        windowRef.current = popup;
        if (import.meta.env.DEV) (window as any).__trackmapPopout = popup;
        plog('window opened');
        popup.document.title = 'Track Map — LMU Telemetry Lab';

        // Copy all existing styles from parent window
        document.querySelectorAll('style, link[rel="stylesheet"]').forEach(el => {
            popup.document.head.appendChild(el.cloneNode(true));
        });

        // Watch for new styles added by Vite HMR
        const observer = new MutationObserver(mutations => {
            for (const m of mutations) {
                m.addedNodes.forEach(node => {
                    if (
                        node instanceof HTMLStyleElement ||
                        (node instanceof HTMLLinkElement && node.rel === 'stylesheet')
                    ) {
                        popup.document.head.appendChild(node.cloneNode(true));
                    }
                });
            }
        });
        observer.observe(document.head, { childList: true });
        observerRef.current = observer;

        // Setup popup body
        popup.document.body.style.margin = '0';
        popup.document.body.style.overflow = 'hidden';

        const container = popup.document.createElement('div');
        container.id = 'popout-root';
        popup.document.body.appendChild(container);

        // Surface popup-window errors in the main console (popup console is hard to reach)
        popup.addEventListener('error', (e) => console.error('[popout] error:', e.message, e.error?.stack));
        popup.addEventListener('unhandledrejection', (e: any) => console.error('[popout] rejection:', e.reason));

        // Create an independent React root in the popup (events work natively)
        const reactRoot = createRoot(container, {
            onUncaughtError: (e: any, info) => plog('uncaught render error:', e, '|STACK|', e?.stack?.slice(0, 600), '|COMP|', info.componentStack?.slice(0, 200)),
            onRecoverableError: (e) => plog('recoverable:', e),
        });
        rootRef.current = reactRoot;
        reactRoot.render(<PopoutContent />);
        plog('react root mounted');

        popup.addEventListener('beforeunload', () => setOpen(false));

        return () => {
            cleanup();
            if (windowRef.current && !windowRef.current.closed) {
                windowRef.current.close();
            }
            windowRef.current = null;
        };
    }, [isOpen, setOpen, cleanup]);

    return null;
}
