import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { RequireAuth } from './components/RequireAuth';
import { CircuitsSheet, ComposerRoute, ConsoleApp, JournalSheet } from './console/ConsoleApp';
import { ConsumePage } from './pages/ConsumePage';
import { RegisterPage } from './pages/RegisterPage';
import { SignInPage } from './pages/SignInPage';

const root = document.getElementById('root');
if (!root) throw new Error('#root element not found');

// Every route here needs a Django shell in project/urls.py, and vice versa.
createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/signin" element={<SignInPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/auth/consume" element={<ConsumePage />} />
        <Route element={<RequireAuth><ConsoleApp /></RequireAuth>}>
          <Route path="/journal/" element={<JournalSheet />} />
          <Route path="/circuits/" element={<CircuitsSheet />} />
          <Route path="/circuits/new/" element={<ComposerRoute />} />
          <Route path="/circuits/:id/" element={<ComposerRoute />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
