import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { BrowserRouter } from 'react-router';
import { App } from './app/App';
import 'antd/dist/reset.css';
import './styles/global.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#136d62', borderRadius: 6 } }}>
      <BrowserRouter><App /></BrowserRouter>
    </ConfigProvider>
  </StrictMode>,
);
