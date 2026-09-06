import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

// Polls /queue/status once a second rather than opening a WebSocket just
// to learn "you've been matched" -- the match WebSocket (MatchView) is
// worth its own connection because it streams for the whole duel; polling
// a few times while waiting in a lobby queue doesn't justify the same.
const POLL_INTERVAL_MS = 1000;

export default function Lobby({ session, onLogout, onMatched }) {
  const [rating, setRating] = useState(null);
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState(null);
  const pollRef = useRef(null);

  useEffect(() => {
    api.getRating(session.user_id).then(setRating).catch(() => {});
  }, [session.user_id]);

  useEffect(() => () => clearInterval(pollRef.current), []);

  async function handleJoin() {
    setError(null);
    try {
      await api.joinQueue(session.token, session.email.split("@")[0]);
      setWaiting(true);
      pollRef.current = setInterval(async () => {
        try {
          const status = await api.queueStatus(session.token);
          if (status.status === "matched") {
            clearInterval(pollRef.current);
            onMatched(status.match_id);
          }
        } catch {
          clearInterval(pollRef.current);
          setWaiting(false);
        }
      }, POLL_INTERVAL_MS);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleCancel() {
    clearInterval(pollRef.current);
    setWaiting(false);
    await api.leaveQueue(session.token).catch(() => {});
  }

  return (
    <div className="centered">
      <div className="card">
        <h1>CodeDuel</h1>
        <p className="subtitle">{session.email}</p>
        {rating && (
          <p className="rating">
            Rating: <strong>{rating.rating}</strong> ({rating.matches_played} matches played)
          </p>
        )}
        {error && <div className="error">{error}</div>}

        {!waiting && (
          <button onClick={handleJoin}>Find Match</button>
        )}
        {waiting && (
          <>
            <p className="pulse">Waiting for an opponent…</p>
            <button className="secondary" onClick={handleCancel}>
              Cancel
            </button>
          </>
        )}

        <button className="link" onClick={onLogout}>
          Log out
        </button>
      </div>
    </div>
  );
}
