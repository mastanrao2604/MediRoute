export default function Spinner({ size = 'md', className = '' }) {
  const sizeClass = {
    sm: 'w-5 h-5 border-2',
    md: 'w-8 h-8 border-4',
    lg: 'w-12 h-12 border-4',
  }[size] ?? 'w-8 h-8 border-4';

  return (
    <div
      className={`${sizeClass} border-indigo-600 border-t-transparent rounded-full animate-spin ${className}`}
    />
  );
}
