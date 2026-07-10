import { useState, useEffect, useCallback } from 'react';
import { motion } from 'framer-motion';
import { Lock, LogOut } from 'lucide-react';
import { handleGlassMouseMove } from '../utils/glassEffect';

const API_BASE = '/api/v1';

export function useAuthState() {
    const [needsAuth, setNeedsAuth] = useState(false);
    const [checked, setChecked] = useState(false);

    const checkAuth = useCallback(async () => {
        try {
            const token = localStorage.getItem('auth_token');
            const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
            const res = await fetch(`${API_BASE}/ping`, { headers });
            setNeedsAuth(res.status === 401);
        } catch {
            setNeedsAuth(false);
        }
        setChecked(true);
    }, []);

    useEffect(() => { checkAuth(); }, [checkAuth]);

    const logout = useCallback(() => {
        localStorage.removeItem('auth_token');
        setNeedsAuth(true);
    }, []);

    return { needsAuth, checked, recheckAuth: checkAuth, logout };
}

interface AuthGateProps {
    onAuthenticated: () => void;
}

export const AuthGate: React.FC<AuthGateProps> = ({ onAuthenticated }) => {
    const [token, setToken] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!token.trim()) return;

        setLoading(true);
        setError('');

        try {
            const res = await fetch(`${API_BASE}/ping`, {
                headers: { Authorization: `Bearer ${token.trim()}` },
            });

            if (res.ok) {
                localStorage.setItem('auth_token', token.trim());
                onAuthenticated();
            } else {
                setError('Invalid token');
            }
        } catch {
            setError('Cannot reach server');
        }
        setLoading(false);
    };

    return (
        <div className="fixed inset-0 bg-[#0a0a0f] flex items-center justify-center p-6">
            <motion.div
                initial={{ scale: 0.9, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                className="w-full max-w-sm glass-container rounded-[2rem] p-8 border border-white/5"
                onMouseMove={handleGlassMouseMove}
            >
                <div className="glass-content flex flex-col items-center">
                    <div className="w-14 h-14 bg-blue-500/10 rounded-2xl flex items-center justify-center mb-6 border border-blue-500/20">
                        <Lock size={28} className="text-blue-400" />
                    </div>

                    <h2 className="text-2xl font-black tracking-tight mb-1 text-white uppercase italic">
                        LMU Telemetry Lab
                    </h2>
                    <p className="text-gray-500 text-sm mb-6 text-center">
                        Enter your access token to continue.
                    </p>

                    {error && (
                        <div className="w-full mb-4 p-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-xs font-bold text-center">
                            {error}
                        </div>
                    )}

                    <form onSubmit={handleSubmit} className="w-full space-y-4">
                        <input
                            autoFocus
                            type="password"
                            value={token}
                            onChange={(e) => setToken(e.target.value)}
                            placeholder="Paste your token here"
                            className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-3 text-white placeholder:text-gray-600 focus:outline-none focus:border-blue-500/50 focus:ring-2 focus:ring-blue-500/10 transition-all font-mono text-sm"
                        />
                        <button
                            type="submit"
                            disabled={loading || !token.trim()}
                            className="w-full py-3 rounded-xl bg-blue-600 text-white font-black hover:bg-blue-500 transition-all shadow-[0_10px_20px_rgba(59,130,246,0.3)] disabled:opacity-50 disabled:cursor-not-allowed uppercase tracking-widest text-xs"
                        >
                            {loading ? 'Checking...' : 'Connect'}
                        </button>
                    </form>
                </div>
            </motion.div>
        </div>
    );
};

export const LogoutButton: React.FC<{ onLogout: () => void }> = ({ onLogout }) => (
    <button
        onClick={onLogout}
        className="p-2 text-gray-500 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-all"
        title="Logout"
    >
        <LogOut size={16} />
    </button>
);
