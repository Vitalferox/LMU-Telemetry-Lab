import { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import ErrorBoundary from './components/ErrorBoundary.tsx'
import { AuthGate, useAuthState } from './components/AuthGate.tsx'

function Root() {
  const { needsAuth, checked, recheckAuth } = useAuthState();

  if (!checked) return null;
  if (needsAuth) return <AuthGate onAuthenticated={recheckAuth} />;
  return <App />;
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <Root />
    </ErrorBoundary>
  </StrictMode>,
)
