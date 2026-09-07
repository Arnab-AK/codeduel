import { useEffect, useState } from "react";
import AuthForm from "./components/AuthForm.jsx";
import Lobby from "./components/Lobby.jsx";
import MatchView from "./components/MatchView.jsx";
import MatrixRain from "./components/MatrixRain.jsx";

const SESSION_KEY = "codeduel_session";

// Deliberately no react-router: the whole app is one linear flow
// (auth -> lobby -> match), so a single piece of state picking which
// screen to render is simpler than a router for three routes that are
// never deep-linked to directly.
export default function App() {
  const [session, setSession] = useState(() => {
    const saved = localStorage.getItem(SESSION_KEY);
    return saved ? JSON.parse(saved) : null;
  });
  const [matchId, setMatchId] = useState(null);

  useEffect(() => {
    if (session) localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    else localStorage.removeItem(SESSION_KEY);
  }, [session]);

  function handleLogout() {
    setMatchId(null);
    setSession(null);
  }

  return (
    <>
      <MatrixRain />
      <div className="scanlines" aria-hidden="true" />
      <div className="app-content">
        {!session && <AuthForm onAuth={setSession} />}
        {session && !matchId && (
          <Lobby session={session} onLogout={handleLogout} onMatched={setMatchId} />
        )}
        {session && matchId && (
          <MatchView session={session} matchId={matchId} onExit={() => setMatchId(null)} />
        )}
      </div>
    </>
  );
}
