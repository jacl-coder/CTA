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
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#197b68', colorInfo: '#197b68', colorText: '#25334a', colorTextSecondary: '#718096', borderRadius: 8, controlHeight: 38, fontFamily: 'Inter, "Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif' }, components: { Button: { fontWeight: 500, primaryShadow: 'none' }, Table: { headerBg: '#f7f9fc', headerColor: '#718096', borderColor: '#edf0f4', cellPaddingBlock: 17 }, Select: { optionSelectedBg: '#e9f3ef' }, Tabs: { inkBarColor: '#197b68' } } }}>
      <BrowserRouter><App /></BrowserRouter>
    </ConfigProvider>
  </StrictMode>,
);
