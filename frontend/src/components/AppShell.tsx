import { NavLink, Outlet, ScrollRestoration } from "react-router-dom";
import { useState } from "react";
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetDescription,
  SheetTrigger,
} from "./ui/sheet";
import { Button } from "./ui";
const navigation = [
  ["/sources", "RSS 来源"],
  ["/messages", "消息"],
  ["/tasks", "任务"],
  ["/settings/providers", "模型配置"],
] as const;
export function AppShell() {
  const [open, setOpen] = useState(false);
  const links = (
    <nav className="nav-list">
      {navigation.map(([to, label]) => (
        <NavLink
          key={to}
          to={to}
          onClick={() => setOpen(false)}
          className={({ isActive }) =>
            isActive ? "nav-link active" : "nav-link"
          }
        >
          {label}
        </NavLink>
      ))}
    </nav>
  );
  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="主导航">
        <div className="brand">
          <span className="brand-mark">R</span>
          <div>
            <strong>RSS 工作台</strong>
            <span>v2 · 本机模式</span>
          </div>
        </div>
        {links}
        <div className="sidebar-footer">本机开发环境</div>
      </aside>
      <header className="mobile-header">
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetTrigger asChild>
            <Button className="secondary">打开导航</Button>
          </SheetTrigger>
          <SheetContent side="left">
            <SheetTitle>RSS 工作台</SheetTitle>
            <SheetDescription>本机开发环境</SheetDescription>
            {links}
          </SheetContent>
        </Sheet>
        <strong>RSS 工作台</strong>
      </header>
      <main className="main-content">
        <Outlet />
        <ScrollRestoration
          getKey={(location) => location.pathname + location.search}
        />
      </main>
    </div>
  );
}
