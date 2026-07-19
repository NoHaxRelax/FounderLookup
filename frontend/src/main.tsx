import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { createBrowserClient } from './api/client'
import './styles.css'

const rootElement = document.getElementById('root')

if (!rootElement) throw new Error('Application root element was not found.')

const client = createBrowserClient(import.meta.env)

createRoot(rootElement).render(
  <StrictMode>
    <App client={client} />
  </StrictMode>,
)
