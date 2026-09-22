import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { RequireAuth } from './components/RequireAuth';
import { ConsumePage } from './pages/ConsumePage';
import { LeadsPage } from './pages/LeadsPage';
import { SignInPage } from './pages/SignInPage';
import './styles.css';

const root = document.getElementById('root');
if (!root) throw new Error('#root element not found');

// Every route here needs a Django shell in project/urls.py, and vice versa.
createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/signin" element={<SignInPage />} />
        <Route path="/auth/consume" element={<ConsumePage />} />
        <Route path="/leads/" element={<RequireAuth><LeadsPage /></RequireAuth>} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
