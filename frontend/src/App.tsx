import { Link, NavLink, Outlet, Route, Routes } from 'react-router-dom';
import Analytics from './pages/Analytics';
import Coach from './pages/Coach';
import Dashboard from './pages/Dashboard';
import ExamRoom from './pages/ExamRoom';
import Lab from './pages/Lab';
import Mocks from './pages/Mocks';
import Practice from './pages/Practice';
import Result from './pages/Result';
import Search from './pages/Search';

export default function App() {
  return (
    <Routes>
      {/* The exam room is full-screen, like the real CBT: no site navigation. */}
      <Route path="/exam/:id" element={<ExamRoom />} />
      <Route element={<Shell />}>
        <Route index element={<Dashboard />} />
        <Route path="practice" element={<Practice />} />
        <Route path="mocks" element={<Mocks />} />
        <Route path="result/:id" element={<Result />} />
        <Route path="analytics" element={<Analytics />} />
        <Route path="search" element={<Search />} />
        <Route path="coach" element={<Coach />} />
        <Route path="lab" element={<Lab />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

function Shell() {
  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-inner">
          <Link to="/" className="brand">
            <span className="brand-mark" aria-hidden>✓</span>
            <span>SSC Practice</span>
          </Link>
          <nav className="nav">
            <NavLink to="/" end>Home</NavLink>
            <NavLink to="/practice">Practice</NavLink>
            <NavLink to="/mocks">Mocks</NavLink>
            <NavLink to="/search">Search</NavLink>
            <NavLink to="/coach">Coach</NavLink>
            <NavLink to="/analytics">Progress</NavLink>
          </nav>
        </div>
      </header>
      <main className="page">
        <Outlet />
      </main>
      <footer className="footer">
        <Link to="/lab">AI Lab</Link> · RAG search, trained topic model, self-learning learner model, Gemini
      </footer>
    </div>
  );
}

function NotFound() {
  return (
    <div className="empty">
      <h1>Page not found</h1>
      <p><Link to="/">Back to home</Link></p>
    </div>
  );
}
