import { useEffect, useState, type ReactNode } from 'react';

interface Props {
  /** Stable key used to remember the collapsed state across reloads. */
  storageKey: string;
  /** Always-visible row. Clicking it toggles the panel. */
  summary: ReactNode;
  children: ReactNode;
  /** Collapsed on first ever view. */
  defaultCollapsed?: boolean;
  className?: string;
  /** Extra controls shown in the summary row that must not toggle the panel. */
  actions?: ReactNode;
}

/**
 * A panel that collapses to its summary row.
 *
 * Every overlay in this viewer competes with the thing it is describing: the scene. So each is
 * collapsible to a single clickable row, and the choice is remembered per panel. On a phone in
 * portrait, three expanded overlays would leave almost no scene visible, which is the case this is
 * really for.
 */
export default function CollapsiblePanel({
  storageKey,
  summary,
  children,
  defaultCollapsed = false,
  className = '',
  actions,
}: Props) {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      const stored = localStorage.getItem(`d3d.panel.${storageKey}`);
      return stored === null ? defaultCollapsed : stored === '1';
    } catch {
      return defaultCollapsed;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(`d3d.panel.${storageKey}`, collapsed ? '1' : '0');
    } catch {
      /* storage may be unavailable; the panel still works, it just forgets */
    }
  }, [collapsed, storageKey]);

  return (
    <section className={`collapsible ${collapsed ? 'is-collapsed' : ''} ${className}`}>
      <div className="collapsible-summary">
        <button
          className="collapsible-toggle"
          onClick={() => setCollapsed((value) => !value)}
          aria-expanded={!collapsed}
          title={collapsed ? 'Expand' : 'Collapse'}
        >
          <span className={`chevron ${collapsed ? 'down' : 'up'}`} aria-hidden="true" />
          <span className="collapsible-summary-content">{summary}</span>
        </button>
        {actions && <div className="collapsible-actions">{actions}</div>}
      </div>
      {!collapsed && <div className="collapsible-body">{children}</div>}
    </section>
  );
}
