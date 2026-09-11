import { BrowserRouter, Route, Routes } from "react-router-dom";
import { TopNav } from "./components/TopNav";
import { Dashboard } from "./pages/Dashboard";
import { Connectors } from "./pages/Connectors";
import { Tickets } from "./pages/Tickets";
import { CheckDetail } from "./pages/CheckDetail";
import { NewCheck } from "./pages/NewCheck";
import { EditCheck } from "./pages/EditCheck";

export default function App() {
  return (
    <BrowserRouter>
      <TopNav />
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/connectors" element={<Connectors />} />
        <Route path="/tickets" element={<Tickets />} />
        <Route path="/checks/new" element={<NewCheck />} />
        <Route path="/checks/:id" element={<CheckDetail />} />
        <Route path="/checks/:id/edit" element={<EditCheck />} />
      </Routes>
    </BrowserRouter>
  );
}
