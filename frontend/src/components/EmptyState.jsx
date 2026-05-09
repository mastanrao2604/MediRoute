export default function EmptyState({ icon = '📭', title, description, action }) {
  return (
    <div className="text-center py-16 px-4">
      <div className="text-4xl mb-3">{icon}</div>
      <p className="text-gray-700 font-medium text-sm">{title}</p>
      {description && (
        <p className="text-gray-400 text-xs mt-1 max-w-xs mx-auto">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
