/**
 * Mobile-first subpage header for recruiter drill-down screens (Applicants, etc.).
 * Sticks below MainLayout's mobile top bar; full-width on desktop.
 */
export default function RecruiterSubpageHeader({
  title,
  subtitle,
  onBack,
  rightSlot = null,
}) {
  return (
    <header
      className="sticky z-40 -mx-4 sm:-mx-6 lg:mx-0 bg-white/95 backdrop-blur-md border-b border-gray-100 shadow-sm top-[calc(3.5rem+env(safe-area-inset-top,0px))] lg:top-0"
    >
      <div className="max-w-3xl mx-auto flex items-center gap-0.5 min-h-14 px-1 sm:px-2">
        <button
          type="button"
          onClick={onBack}
          aria-label="Go back"
          className="flex shrink-0 items-center justify-center w-11 h-11 rounded-full text-gray-900 hover:bg-gray-100 active:bg-gray-200 transition-colors touch-manipulation"
        >
          <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <div className="flex-1 min-w-0 py-2 pr-2">
          <h1 className="text-[17px] font-bold text-gray-900 truncate leading-snug">{title}</h1>
          {subtitle ? (
            <p className="text-sm text-gray-500 truncate leading-snug mt-0.5">{subtitle}</p>
          ) : null}
        </div>
        {rightSlot ? <div className="shrink-0 pr-1">{rightSlot}</div> : null}
      </div>
    </header>
  );
}
