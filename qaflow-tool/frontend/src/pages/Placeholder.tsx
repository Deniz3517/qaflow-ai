interface Props {
  title: string;
  description?: string;
  bullets?: string[];
}

export default function Placeholder({ title, description, bullets = [] }: Props) {
  return (
    <div className="max-w-2xl space-y-4">
      <header>
        <h1 className="text-2xl font-semibold text-ink-100">{title}</h1>
        {description && <p className="text-sm text-ink-400 mt-1">{description}</p>}
      </header>

      <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-5">
        <div className="flex items-center gap-2 text-amber-300 text-xs uppercase tracking-wider font-semibold">
          <span>◔</span><span>Coming in MVP v2</span>
        </div>
        <p className="text-sm text-ink-300 mt-2">
          This page is scaffolded for the next milestone. Per the QAFLOW AI v2.0 spec it will contain:
        </p>
        {bullets.length > 0 && (
          <ul className="mt-3 space-y-1.5 text-sm text-ink-300 list-disc list-inside">
            {bullets.map((b) => <li key={b}>{b}</li>)}
          </ul>
        )}
      </div>
    </div>
  );
}
