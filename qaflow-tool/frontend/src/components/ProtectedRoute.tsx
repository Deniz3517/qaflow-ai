import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../AuthContext";
import type { Role } from "../types";

export default function ProtectedRoute({
  children,
  roles,
}: {
  children: ReactNode;
  roles?: Role[];
}) {
  const { user, loading } = useAuth();
  const loc = useLocation();

  if (loading) {
    return (
      <div className="min-h-full flex items-center justify-center text-ink-400">Loading…</div>
    );
  }
  if (!user) {
    return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  }
  if (roles && !roles.includes(user.role)) {
    return (
      <div className="min-h-full flex items-center justify-center px-6">
        <div className="text-center max-w-md">
          <div className="text-5xl mb-4 text-rose-400">✕</div>
          <h2 className="text-xl text-ink-100 font-semibold">Access denied</h2>
          <p className="text-sm text-ink-400 mt-2">
            This page is restricted to roles: {roles.join(", ")}.
          </p>
          <p className="text-xs text-ink-500 mt-4 font-mono">
            You are signed in as <span className="text-ink-300">{user.role}</span>.
          </p>
        </div>
      </div>
    );
  }
  return <>{children}</>;
}
