import { Check, Columns2, Copy, FileDiff } from 'lucide-react';
import { useMemo, useState } from 'react';

interface DiffViewerProps {
  diff: string;
  defaultMode?: 'unified' | 'split';
  compact?: boolean;
}

interface SplitRow {
  leftLine: string | null;
  rightLine: string | null;
  type: 'context' | 'change' | 'meta';
}

function buildSplitRows(lines: string[]): SplitRow[] {
  const rows: SplitRow[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith('---') || line.startsWith('+++') || line.startsWith('@@')) {
      rows.push({ leftLine: line, rightLine: line, type: 'meta' });
      i += 1;
      continue;
    }

    if (line.startsWith('-') || line.startsWith('+')) {
      const removed: string[] = [];
      const added: string[] = [];
      while (i < lines.length && lines[i].startsWith('-') && !lines[i].startsWith('---')) {
        removed.push(lines[i].slice(1));
        i += 1;
      }
      while (i < lines.length && lines[i].startsWith('+') && !lines[i].startsWith('+++')) {
        added.push(lines[i].slice(1));
        i += 1;
      }
      const maxLen = Math.max(removed.length, added.length);
      for (let k = 0; k < maxLen; k += 1) {
        rows.push({
          leftLine: removed[k] ?? null,
          rightLine: added[k] ?? null,
          type: 'change',
        });
      }
      continue;
    }

    const text = line.startsWith(' ') ? line.slice(1) : line;
    rows.push({ leftLine: text, rightLine: text, type: 'context' });
    i += 1;
  }

  return rows;
}

export function DiffViewer({ diff, defaultMode = 'unified', compact = false }: DiffViewerProps) {
  const [mode, setMode] = useState<'unified' | 'split'>(defaultMode);
  const [copied, setCopied] = useState(false);

  const lines = useMemo(() => (diff ? diff.replace(/\r\n/g, '\n').split('\n') : []), [diff]);
  const splitRows = useMemo(() => buildSplitRows(lines), [lines]);

  if (!diff || lines.length === 0) {
    return (
      <div className="px-3 py-4 text-xs text-slate-400 bg-[#0b1019] rounded border border-slate-800/80 font-mono">
        No YAML diff recorded for this entry.
      </div>
    );
  }

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(diff);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard API unavailable in restricted contexts
    }
  };

  return (
    <div className="bg-[#0b1019] border border-slate-800/90 rounded-md overflow-hidden">
      <div className="flex items-center justify-between gap-4 px-3 py-2 border-b border-slate-800/90 bg-[#0f1521]">
        <div className="flex items-center gap-1 bg-[#090d14] p-0.5 rounded border border-slate-800/80">
          <button
            type="button"
            onClick={() => setMode('unified')}
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded transition-colors whitespace-nowrap shrink-0 ${
              mode === 'unified'
                ? 'bg-slate-800 text-slate-100'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <FileDiff className="w-3.5 h-3.5" aria-hidden="true" />
            Unified
          </button>
          <button
            type="button"
            onClick={() => setMode('split')}
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded transition-colors whitespace-nowrap shrink-0 ${
              mode === 'split'
                ? 'bg-slate-800 text-slate-100'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Columns2 className="w-3.5 h-3.5" aria-hidden="true" />
            Side by side
          </button>
        </div>

        <button
          type="button"
          onClick={handleCopy}
          aria-label="Copy diff to clipboard"
          className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium text-slate-300 hover:text-white bg-slate-800/60 hover:bg-slate-800 rounded transition-colors whitespace-nowrap shrink-0"
        >
          {copied ? (
            <>
              <Check className="w-3.5 h-3.5 text-emerald-400" aria-hidden="true" />
              Copied
            </>
          ) : (
            <>
              <Copy className="w-3.5 h-3.5" aria-hidden="true" />
              Copy diff
            </>
          )}
        </button>
      </div>

      <div className={`overflow-x-auto font-mono text-xs leading-5 ${compact ? 'max-h-64' : 'max-h-96'} overflow-y-auto`}>
        {mode === 'unified' ? (
          <table className="w-full border-collapse">
            <tbody>
              {lines.map((line, idx) => {
                const isAdd = line.startsWith('+') && !line.startsWith('+++');
                const isDel = line.startsWith('-') && !line.startsWith('---');
                const isMeta =
                  line.startsWith('@@') || line.startsWith('---') || line.startsWith('+++');

                let rowClass = 'text-slate-300';
                if (isAdd) rowClass = 'bg-emerald-950/45 text-emerald-200';
                else if (isDel) rowClass = 'bg-rose-950/45 text-rose-200';
                else if (isMeta) rowClass = 'bg-slate-900/70 text-sky-300/90';

                return (
                  <tr key={idx} className={rowClass}>
                    <td className="select-none w-10 px-2.5 py-0.5 text-right text-slate-600 border-r border-slate-800/70 tabular-nums">
                      {idx + 1}
                    </td>
                    <td className="px-3 py-0.5 whitespace-pre">{line || ' '}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <table className="w-full border-collapse table-fixed min-w-[640px]">
            <thead>
              <tr className="border-b border-slate-800/80 text-left text-[11px] text-slate-400 bg-[#0d131e]">
                <th className="w-1/2 px-3 py-1.5 font-medium border-r border-slate-800/80">
                  Before
                </th>
                <th className="w-1/2 px-3 py-1.5 font-medium">Proposed</th>
              </tr>
            </thead>
            <tbody>
              {splitRows.map((row, idx) => {
                if (row.type === 'meta') {
                  return (
                    <tr key={idx} className="bg-slate-900/70 text-sky-300/90">
                      <td colSpan={2} className="px-3 py-0.5 whitespace-pre">
                        {row.leftLine}
                      </td>
                    </tr>
                  );
                }

                const leftRemoved = row.type === 'change' && row.leftLine !== null;
                const rightAdded = row.type === 'change' && row.rightLine !== null;

                return (
                  <tr key={idx}>
                    <td
                      className={`px-3 py-0.5 whitespace-pre border-r border-slate-800/70 align-top ${
                        leftRemoved
                          ? 'bg-rose-950/45 text-rose-200'
                          : row.type === 'change'
                            ? 'bg-slate-950/40 text-slate-600'
                            : 'text-slate-300'
                      }`}
                    >
                      {leftRemoved ? `- ${row.leftLine}` : row.leftLine ?? ''}
                    </td>
                    <td
                      className={`px-3 py-0.5 whitespace-pre align-top ${
                        rightAdded
                          ? 'bg-emerald-950/45 text-emerald-200'
                          : row.type === 'change'
                            ? 'bg-slate-950/40 text-slate-600'
                            : 'text-slate-300'
                      }`}
                    >
                      {rightAdded ? `+ ${row.rightLine}` : row.rightLine ?? ''}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
