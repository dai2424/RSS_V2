import { lazy, Suspense } from "react";
import {
  Navigate,
  Route,
  createRoutesFromElements,
  createBrowserRouter,
  RouterProvider,
} from "react-router-dom";
import { AppShell } from "./components/AppShell";
const Keywords = lazy(() =>
  import("./features/keywords/KeywordsPage").then((m) => ({
    default: m.KeywordsPage,
  })),
);
const Sources = lazy(() =>
  import("./features/sources/SourcesPage").then((m) => ({
    default: m.SourcesPage,
  })),
);
const SourceForm = lazy(() =>
  import("./features/sources/SourceFormPage").then((m) => ({
    default: m.SourceFormPage,
  })),
);
const SourceDetail = lazy(() =>
  import("./features/sources/SourceDetailPage").then((m) => ({
    default: m.SourceDetailPage,
  })),
);
const Messages = lazy(() =>
  import("./features/messages/MessagesPage").then((m) => ({
    default: m.MessagesPage,
  })),
);
const MessageDetail = lazy(() =>
  import("./features/messages/MessageDetailPage").then((m) => ({
    default: m.MessageDetailPage,
  })),
);
const Tasks = lazy(() =>
  import("./features/tasks/TasksPage").then((m) => ({ default: m.TasksPage })),
);
const Providers = lazy(() =>
  import("./features/providers/ProvidersPage").then((m) => ({
    default: m.ProvidersPage,
  })),
);
const Prompts = lazy(() =>
  import("./features/prompts/PromptsPage").then((m) => ({
    default: m.PromptsPage,
  })),
);
const TaskSettings = lazy(() =>
  import("./features/taskSettings/TaskSettingsPage").then((m) => ({
    default: m.TaskSettingsPage,
  })),
);
const router = createBrowserRouter(
  createRoutesFromElements(
    <Route element={<AppShell />}>
      <Route index element={<Navigate to="/sources" replace />} />
      <Route path="/sources" element={<Sources />} />
      <Route path="/sources/new" element={<SourceForm />} />
      <Route path="/sources/:sourceId" element={<SourceDetail />} />
      <Route path="/sources/:sourceId/edit" element={<SourceForm />} />
      <Route path="/messages" element={<Messages />} />
      <Route path="/messages/:messageId" element={<MessageDetail />} />
      <Route path="/tasks" element={<Tasks />} />
      <Route path="/settings/providers" element={<Providers />} />
      <Route path="/settings/prompts" element={<Prompts />} />
      <Route path="/settings/tasks" element={<TaskSettings />} />
      <Route path="/settings/keywords" element={<Keywords />} />
      <Route path="*" element={<Navigate to="/sources" replace />} />
    </Route>,
  ),
);
export function App() {
  return (
    <Suspense fallback={<div className="loading">加载页面…</div>}>
      <RouterProvider router={router} />
    </Suspense>
  );
}
