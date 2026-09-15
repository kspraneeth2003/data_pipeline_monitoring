import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { TopNav } from "./components/TopNav";
import { Projects } from "./pages/Projects";
import { NewProject } from "./pages/NewProject";
import { ProjectOverview } from "./pages/ProjectOverview";
import { ProjectTickets } from "./pages/ProjectTickets";
import { ProjectSettings } from "./pages/ProjectSettings";
import { NewDatabase } from "./pages/NewDatabase";
import { DatabaseChecks } from "./pages/DatabaseChecks";
import { DatabaseSettings } from "./pages/DatabaseSettings";
import { Connectors } from "./pages/Connectors";
import { CheckDetail } from "./pages/CheckDetail";
import { NewCheck } from "./pages/NewCheck";
import { EditCheck } from "./pages/EditCheck";

/**
 * Routes mirror the hierarchy - project -> database -> check - so the URL
 * always says where you are and every level above is reachable by trimming the
 * path.
 *
 * Connectors sit outside the tree because one connector serves many databases
 * across many projects.
 */
export default function App() {
  return (
    <BrowserRouter>
      <TopNav />
      <Routes>
        <Route path="/" element={<Projects />} />
        <Route path="/projects/new" element={<NewProject />} />
        <Route path="/projects/:slug" element={<ProjectOverview />} />
        <Route path="/projects/:slug/tickets" element={<ProjectTickets />} />
        <Route path="/projects/:slug/settings" element={<ProjectSettings />} />

        <Route path="/projects/:slug/databases/new" element={<NewDatabase />} />
        <Route path="/projects/:slug/databases/:dbSlug" element={<DatabaseChecks />} />
        <Route path="/projects/:slug/databases/:dbSlug/settings" element={<DatabaseSettings />} />
        <Route path="/projects/:slug/databases/:dbSlug/checks/new" element={<NewCheck />} />
        <Route path="/projects/:slug/databases/:dbSlug/checks/:id" element={<CheckDetail />} />
        <Route path="/projects/:slug/databases/:dbSlug/checks/:id/edit" element={<EditCheck />} />

        <Route path="/connectors" element={<Connectors />} />

        {/* Pre-hierarchy URLs. Bookmarks should land somewhere useful, not 404. */}
        <Route path="/projects/:slug/checks/*" element={<Navigate to=".." relative="path" replace />} />
        <Route path="/checks/*" element={<Navigate to="/" replace />} />
        <Route path="/tickets" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
