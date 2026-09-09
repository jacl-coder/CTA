import { Link, Route, Routes } from 'react-router';
import { OverviewPage } from '../features/overview/OverviewPage';

export function App() {
  return (
    <div className="workspace">
      <header className="topbar">
        <Link to="/" className="brand"><strong>CTA</strong><span>期货风控工作台</span></Link>
        <span className="environment">本地演示环境</span>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="*" element={<><h1>页面不存在</h1><Link to="/">返回工作台</Link></>} />
        </Routes>
      </main>
    </div>
  );
}
