import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { TopNav } from "./components/TopNav";
import { Projects } from "./pages/Projects";
import { NewProject } from "./pages/NewProject";
import { NewProjectManual } from "./pages/NewProjectManual";
import { ProjectOverview } from "./pages/ProjectOverview";
import { ProjectIncidents } from "./pages/ProjectIncidents";
import { ProjectRevisions } from "./pages/ProjectRevisions";
import { ProjectTickets } from "./pages/ProjectTickets";
import { ProjectSettings } from "./pages/ProjectSettings";
import { NewDatabase } from "./pages/NewDatabase";
import { DatabaseChecks } from "./pages/DatabaseChecks";
import { DatabaseSettings } from "./pages/DatabaseSettings";
import { ProjectConnections } from "./pages/ProjectConnections";
import { CheckDetail } from "./pages/CheckDetail";
import { NewCheck } from "./pages/NewCheck";
import { EditCheck } from "./pages/EditCheck";

/**
 * Routes mirror the hierarchy - project -> database -> check - so the URL
 * always says where you are and every level above is reachable by trimming the
 * path.
 *
 * Connections live inside a project too: connecting a warehouse is part of
 * setting a project up, not a separate administrative step done elsewhere.
 */
export default function App() {
  return (
    <BrowserRouter>
      <TopNav />
      <Routes>
        <Route path="/" element={<Projects />} />
        <Route path="/projects/new" element={<NewProject />} />
        <Route path="/projects/new/manual" element={<NewProjectManual />} />
        <Route path="/projects/:slug" element={<ProjectOverview />} />
        <Route path="/projects/:slug/tickets" element={<ProjectTickets />} />
        <Route path="/projects/:slug/incidents" element={<ProjectIncidents />} />
        <Route path="/projects/:slug/changes" element={<ProjectRevisions />} />
        <Route path="/projects/:slug/settings" element={<ProjectSettings />} />
        <Route path="/projects/:slug/connections" element={<ProjectConnections />} />

        <Route path="/projects/:slug/databases/new" element={<NewDatabase />} />
        <Route path="/projects/:slug/databases/:dbSlug" element={<DatabaseChecks />} />
        <Route path="/projects/:slug/databases/:dbSlug/settings" element={<DatabaseSettings />} />
        <Route path="/projects/:slug/databases/:dbSlug/checks/new" element={<NewCheck />} />
        <Route path="/projects/:slug/databases/:dbSlug/checks/:id" element={<CheckDetail />} />
        <Route path="/projects/:slug/databases/:dbSlug/checks/:id/edit" element={<EditCheck />} />

        {/* Pre-hierarchy URLs. Bookmarks should land somewhere useful, not 404.
            /connectors is gone entirely - connections now belong to a project. */}
        <Route path="/connectors" element={<Navigate to="/" replace />} />
        <Route path="/projects/:slug/checks/*" element={<Navigate to=".." relative="path" replace />} />
        <Route path="/checks/*" element={<Navigate to="/" replace />} />
        <Route path="/tickets" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
