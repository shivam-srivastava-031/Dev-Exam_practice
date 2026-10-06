import { useEffect } from 'react';
import { Link, Navigate, NavLink, Outlet, Route, Routes, useLocation, useParams } from 'react-router-dom';
import { mockUrl, resultUrl, setPageTitle } from './lib/urls';
import Analytics from './pages/Analytics';
import Coach from './pages/Coach';
import Dashboard from './pages/Dashboard';
import ExamRoom from './pages/ExamRoom';
import Lab from './pages/Lab';
import Mocks from './pages/Mocks';
import Practice from './pages/Practice';
import Result from './pages/Result';
import Search from './pages/Search';

// Every route is listed in lib/urls.ts together with the helpers that build its links.
export default function App() {
  return (
    <Routes>
      {/* The exam room is full-screen, like the real CBT: no site navigation. */}
      <Route path="/mock/:id" element={<ExamRoom />} />
      <Route element={<Shell />}>
        <Route index element={<Dashboard />} />

        <Route path="practice" element={<Practice key="all" />} />
        <Route path="practice/smart" element={<Practice key="smart" mode="smart" />} />
        <Route path="practice/ai" element={<Practice key="ai" mode="ai" />} />
        <Route path="practice/paper/:paper" element={<Practice key="paper" mode="paper" />} />
        <Route path="practice/similar/:id" element={<Practice key="similar" mode="similar" />} />
        <Route path="practice/:subject" element={<Practice key="subject" />} />
        <Route path="practice/:subject/:chapter" element={<Practice key="chapter" />} />
        <Route path="question/:id" element={<Practice key="question" mode="question" />} />

        <Route path="mocks" element={<Mocks />} />
        <Route path="mocks/:exam" element={<Mocks />} />
        <Route path="mocks/:exam/:stage" element={<Mocks />} />
        <Route path="mock/:id/result" element={<Result />} />

        <Route path="search" element={<Search />} />
        <Route path="coach" element={<Coach />} />
        <Route path="progress" element={<Analytics />} />
        <Route path="ai-lab" element={<Lab />} />

        {/* Older URLs keep working. */}
        <Route path="analytics" element={<Navigate replace to="/progress" />} />
        <Route path="lab" element={<Navigate replace to="/ai-lab" />} />
        <Route path="exam/:id" element={<Redirect to={mockUrl} />} />
        <Route path="result/:id" element={<Redirect to={resultUrl} />} />

        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

function Redirect({ to }: { to: (id: string) => string }) {
  const { id = '' } = useParams();
  return <Navigate replace to={to(id)} />;
}

function Shell() {
  const { pathname } = useLocation();
  useEffect(() => window.scrollTo(0, 0), [pathname]);
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
            <NavLink to="/practice" className={({ isActive }) => (isActive || pathname.startsWith('/question') ? 'active' : '')}>Practice</NavLink>
            <NavLink to="/mocks" className={({ isActive }) => (isActive || pathname.startsWith('/mock/') ? 'active' : '')}>Mocks</NavLink>
            <NavLink to="/search">Search</NavLink>
            <NavLink to="/coach">Coach</NavLink>
            <NavLink to="/progress">Progress</NavLink>
          </nav>
        </div>
      </header>
      <main className="page">
        <Outlet />
      </main>
      <footer className="footer">
        <Link to="/ai-lab">AI Lab</Link> · RAG search, trained topic model, self-learning learner model, Gemini
      </footer>
    </div>
  );
}

function NotFound() {
  useEffect(() => setPageTitle('Page not found'), []);
  return (
    <div className="empty">
      <h1>Page not found</h1>
      <p><Link to="/">Back to home</Link></p>
    </div>
  );
}
