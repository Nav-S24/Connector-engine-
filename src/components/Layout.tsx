import {
  Activity,
  BarChart3,
  FileSearch,
  GitCompareArrows,
  LayoutGrid,
  Menu,
  RefreshCw,
  Terminal,
  X,
} from 'lucide-react';
import { type ReactNode, useState } from 'react';

export type PageId = 'overview' | 'runs' | 'drift-repairs' | 'audit' | 'benchmarks';

interface NavItem {
  id: PageId;
  label: string;
  Icon: typeof LayoutGrid;
}

const NAV_ITEMS: NavItem[] = [
  { id: 'overview', label: 'Overview', Icon: LayoutGrid },
  { id: 'runs', label: 'Runs', Icon: Activity },
  { id: 'drift-repairs', label: 'Drift & Repairs', Icon: GitCompareArrows },
  { id: 'audit', label: 'Audit Log', Icon: FileSearch },
  { id: 'benchmarks', label: 'Benchmarks', Icon: BarChart3 },
];

interface LayoutProps {
  activePage: PageId;
  onNavigate: (page: PageId) => void;
  isMock: boolean;
  onRefresh: () => void;
  refreshing?: boolean;
  children: ReactNode;
}

export function Layout({
  activePage,
  onNavigate,
  isMock,
  onRefresh,
  refreshing = false,
  children,
}: LayoutProps) {
  const [mobileOpen, setMobileOpen] = useState(false);

  const currentNav = NAV_ITEMS.find((item) => item.id === activePage) ?? NAV_ITEMS[0];

  const handleSelect = (id: PageId) => {
    onNavigate(id);
    setMobileOpen(false);
  };

  const navContent = (
    <div className="flex flex-col h-full bg-[#0c111b] border-r border-slate-800/90">
      <div className="h-14 px-5 flex items-center gap-2.5 border-b border-slate-800/90">
        <Terminal className="w-4 h-4 text-sky-400 shrink-0" aria-hidden="true" />
        <span className="font-display text-sm font-semibold tracking-tight text-slate-100 whitespace-nowrap">
          Connector Control Room
        </span>
      </div>

      <nav aria-label="Primary navigation" className="flex-1 px-3 py-4 space-y-1">
        {NAV_ITEMS.map((item) => {
          const Icon = item.Icon;
          const active = item.id === activePage;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => handleSelect(item.id)}
              aria-current={active ? 'page' : undefined}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md text-sm font-medium transition-colors text-left whitespace-nowrap ${
                active
                  ? 'bg-sky-500/15 text-sky-300 border border-sky-500/30'
                  : 'text-slate-400 hover:text-slate-100 hover:bg-slate-800/50 border border-transparent'
              }`}
            >
              <Icon
                className={`w-4 h-4 shrink-0 ${active ? 'text-sky-400' : 'text-slate-500'}`}
                aria-hidden="true"
              />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>
    </div>
  );

  return (
    <div className="min-h-screen bg-[#090d14] text-slate-100 flex">
      {/* Desktop sidebar */}
      <aside className="hidden lg:block w-60 shrink-0 sticky top-0 h-screen">
        {navContent}
      </aside>

      {/* Tablet / mobile slide-over sidebar */}
      {mobileOpen ? (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <div
            className="fixed inset-0 bg-black/65"
            onClick={() => setMobileOpen(false)}
            aria-hidden="true"
          />
          <aside className="relative z-10 w-64 h-full">
            <div className="absolute top-3 right-3 z-20">
              <button
                type="button"
                onClick={() => setMobileOpen(false)}
                aria-label="Close navigation menu"
                className="p-1.5 rounded text-slate-400 hover:text-slate-100 hover:bg-slate-800"
              >
                <X className="w-4 h-4" aria-hidden="true" />
              </button>
            </div>
            {navContent}
          </aside>
        </div>
      ) : null}

      <div className="flex-1 min-w-0 flex flex-col">
        <header className="h-14 sticky top-0 z-20 bg-[#090d14]/95 backdrop-blur border-b border-slate-800/90 px-4 sm:px-6 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <button
              type="button"
              onClick={() => setMobileOpen(true)}
              aria-label="Open navigation menu"
              className="lg:hidden p-2 -ml-2 rounded text-slate-400 hover:text-slate-100 hover:bg-slate-800/70"
            >
              <Menu className="w-4 h-4" aria-hidden="true" />
            </button>
            <h1 className="text-sm sm:text-base font-semibold text-slate-100 truncate">
              {currentNav.label}
            </h1>
          </div>

          <div className="flex items-center gap-3 shrink-0">
            {isMock ? (
              <span
                title="Control API unreachable or VITE_USE_MOCK=true — displaying built-in sample data"
                className="px-2.5 py-1 text-xs font-medium rounded bg-amber-500/15 text-amber-300 border border-amber-500/30 whitespace-nowrap shrink-0"
              >
                Demo data
              </span>
            ) : null}

            <button
              type="button"
              onClick={onRefresh}
              disabled={refreshing}
              className="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-xs font-medium rounded bg-slate-800/90 hover:bg-slate-800 text-slate-200 border border-slate-700/80 transition-colors disabled:opacity-60 whitespace-nowrap shrink-0"
            >
              <RefreshCw
                className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin motion-reduce:animate-none' : ''}`}
                aria-hidden="true"
              />
              Refresh
            </button>
          </div>
        </header>

        <main className="flex-1 px-4 sm:px-6 py-6 max-w-[1440px] w-full mx-auto">
          {children}
        </main>
      </div>
    </div>
  );
}
