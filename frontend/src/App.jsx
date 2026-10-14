import AnalyticsDashboard from "./components/AnalyticsDashboard.jsx";
import ShortenForm from "./components/ShortenForm.jsx";

export default function App() {
  return (
    <div className="min-h-screen bg-gray-100 py-8 px-4">
      <ShortenForm />
      <AnalyticsDashboard />
    </div>
  );
}
