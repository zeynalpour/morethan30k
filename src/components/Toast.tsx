interface ToastProps {
  message: string;
  type: "success" | "error";
}

export function Toast({ message, type }: ToastProps) {
  return (
    <div
      className="fixed top-4 left-1/2 -translate-x-1/2 z-50 px-4 py-3 rounded-xl text-sm font-medium shadow-lg animate-fadeIn"
      style={{
        background: type === "success" ? "#2e7d32" : "#c62828",
        color: "#fff",
        maxWidth: "90vw",
        textAlign: "center",
      }}
    >
      {type === "success" ? "✅ " : "⚠️ "}
      {message}
    </div>
  );
}
