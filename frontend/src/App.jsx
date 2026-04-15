import { Routes, Route, Navigate } from 'react-router-dom'
import AppLayout from './components/Layout/AppLayout'
import SettingsPage from './pages/SettingsPage'
import RecordPage from './pages/RecordPage'
import GalleryPage from './pages/GalleryPage'
import AutoCADEditorPage from './pages/AutoCADEditorPage'

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/"                          element={<Navigate to="/record" replace />} />
        <Route path="/record"                    element={<RecordPage />} />
        <Route path="/record/editor/:sessionId"  element={<AutoCADEditorPage />} />
        <Route path="/gallery"                   element={<GalleryPage />} />
        <Route path="/gallery/editor/:sessionId" element={<AutoCADEditorPage />} />
        <Route path="/settings"                  element={<SettingsPage />} />
        <Route path="*"                          element={<Navigate to="/record" replace />} />
      </Route>
    </Routes>
  )
}
