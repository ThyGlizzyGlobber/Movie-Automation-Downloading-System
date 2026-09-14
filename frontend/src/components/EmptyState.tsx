import './StateMessage.css'

export default function EmptyState({ message }: { message: string }) {
  return <div className="empty">{message}</div>
}
