import React, { useEffect } from 'react';
import { useTelemetryStore } from '../store/telemetryStore';
import { Loader2, AlertTriangle, CheckCircle, Info, Zap, Brain, Wrench, BarChart3 } from 'lucide-react';
import { handleGlassMouseMove } from '../utils/glassEffect';
import type { AnalysisResult, AnalysisSection, Recommendation } from '../types';

const severityConfig = {
    info: { icon: Info, bg: 'bg-blue-500/10', border: 'border-blue-500/20', text: 'text-blue-400', dot: 'bg-blue-400' },
    warning: { icon: AlertTriangle, bg: 'bg-amber-500/10', border: 'border-amber-500/20', text: 'text-amber-400', dot: 'bg-amber-400' },
    success: { icon: CheckCircle, bg: 'bg-emerald-500/10', border: 'border-emerald-500/20', text: 'text-emerald-400', dot: 'bg-emerald-400' },
};

const priorityConfig = {
    P1: { bg: 'bg-red-500/15', border: 'border-red-500/30', badge: 'bg-red-500', text: 'text-red-300' },
    P2: { bg: 'bg-amber-500/10', border: 'border-amber-500/25', badge: 'bg-amber-500', text: 'text-amber-300' },
    P3: { bg: 'bg-blue-500/10', border: 'border-blue-500/20', badge: 'bg-blue-500', text: 'text-blue-300' },
};

const SectionCard: React.FC<{ section: AnalysisSection }> = ({ section }) => {
    const config = severityConfig[section.severity] || severityConfig.info;
    const Icon = config.icon;
    return (
        <div
            className={`${config.bg} border ${config.border} rounded-xl p-4 transition-all duration-200 hover:bg-white/5`}
            onMouseMove={(e) => handleGlassMouseMove(e, 0.08)}
        >
            <div className="flex items-center gap-2 mb-2">
                <Icon className={`w-4 h-4 ${config.text}`} />
                <h4 className={`text-sm font-semibold ${config.text}`}>{section.title}</h4>
            </div>
            <p className="text-[13px] text-gray-300 leading-relaxed whitespace-pre-wrap">{section.content}</p>
        </div>
    );
};

const RecommendationCard: React.FC<{ rec: Recommendation }> = ({ rec }) => {
    const config = priorityConfig[rec.priority] || priorityConfig.P3;
    return (
        <div className={`${config.bg} border ${config.border} rounded-lg p-3 flex items-start gap-3`}>
            <span className={`${config.badge} text-white text-[10px] font-black px-1.5 py-0.5 rounded shrink-0 mt-0.5`}>
                {rec.priority}
            </span>
            <div className="min-w-0">
                <p className="text-[13px] text-white font-medium">{rec.action}</p>
                <p className="text-[11px] text-gray-400 mt-1">{rec.reason}</p>
            </div>
        </div>
    );
};

const ResultView: React.FC<{ result: AnalysisResult }> = ({ result }) => (
    <div className="flex flex-col gap-4 animate-in fade-in duration-300">
        {/* Summary */}
        <div
            className="glass-container p-5 rounded-2xl border border-white/10"
            onMouseMove={(e) => handleGlassMouseMove(e, 0.1)}
        >
            <h3 className="text-sm font-semibold text-white/60 uppercase tracking-wider mb-2">Résumé</h3>
            <p className="text-[14px] text-gray-200 leading-relaxed">{result.summary}</p>
        </div>

        {/* Sections */}
        {result.sections.length > 0 && (
            <div className="flex flex-col gap-3">
                {result.sections.map((s, i) => <SectionCard key={i} section={s} />)}
            </div>
        )}

        {/* Recommendations */}
        {result.recommendations.length > 0 && (
            <div
                className="glass-container p-5 rounded-2xl border border-white/10"
                onMouseMove={(e) => handleGlassMouseMove(e, 0.1)}
            >
                <h3 className="text-sm font-semibold text-white/60 uppercase tracking-wider mb-3">Recommandations</h3>
                <div className="flex flex-col gap-2">
                    {result.recommendations.map((r, i) => <RecommendationCard key={i} rec={r} />)}
                </div>
            </div>
        )}

        {/* Cost footer */}
        <div className="flex items-center justify-end gap-4 text-[11px] text-gray-500 px-1">
            {result.model && <span>{result.model}</span>}
            <span>{result.tokens_in.toLocaleString()} in / {result.tokens_out.toLocaleString()} out</span>
            <span>~{result.cost_eur.toFixed(3)} EUR</span>
        </div>
    </div>
);

export const RaceEngineerTab: React.FC = () => {
    const selectedLapIdx = useTelemetryStore(s => s.selectedLapIdx);
    const referenceLapIdx = useTelemetryStore(s => s.referenceLapIdx);
    const referenceLap = useTelemetryStore(s => s.referenceLap);
    const laps = useTelemetryStore(s => s.laps);
    const isAnalyzing = useTelemetryStore(s => s.isAnalyzing);
    const analysisError = useTelemetryStore(s => s.analysisError);
    const raceEngineerResult = useTelemetryStore(s => s.raceEngineerResult);
    const aiCoachConfigured = useTelemetryStore(s => s.aiCoachConfigured);
    const analyzeLap = useTelemetryStore(s => s.analyzeLap);
    const analyzeSession = useTelemetryStore(s => s.analyzeSession);
    const adviseSetup = useTelemetryStore(s => s.adviseSetup);
    const checkAiCoachStatus = useTelemetryStore(s => s.checkAiCoachStatus);

    useEffect(() => {
        if (aiCoachConfigured === null) checkAiCoachStatus();
    }, [aiCoachConfigured, checkAiCoachStatus]);

    const currentLap = laps.find(l => l.lap === selectedLapIdx);
    const refIdx = referenceLapIdx ?? referenceLap?.lap ?? null;

    const formatTime = (s: number) => {
        const min = Math.floor(s / 60);
        const sec = (s % 60).toFixed(3);
        return `${min}:${sec.padStart(6, '0')}`;
    };

    const disabled = isAnalyzing || aiCoachConfigured === false;

    return (
        <div className="flex flex-col gap-5 max-w-3xl mx-auto w-full py-2">
            {/* Header */}
            <div className="flex items-center gap-3 mb-1">
                <Brain className="w-5 h-5 text-blue-400" />
                <h2 className="text-lg font-bold text-white">Race Engineer</h2>
                {aiCoachConfigured === false && (
                    <span className="text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 px-2 py-0.5 rounded-full">
                        API key manquante
                    </span>
                )}
            </div>

            {/* Lap info */}
            {currentLap && (
                <div className="flex items-center gap-3 text-[13px] text-gray-400">
                    <span>Tour <span className="text-white font-semibold">#{selectedLapIdx}</span></span>
                    <span className="text-white/40">|</span>
                    <span className="text-white font-mono">{formatTime(currentLap.duration)}</span>
                    {refIdx !== null && (
                        <>
                            <span className="text-white/40">|</span>
                            <span>Ref: #{refIdx}</span>
                        </>
                    )}
                </div>
            )}

            {/* Action buttons */}
            <div className="grid grid-cols-3 gap-3">
                <button
                    onClick={() => selectedLapIdx !== null && analyzeLap(selectedLapIdx, refIdx ?? undefined)}
                    disabled={disabled || selectedLapIdx === null}
                    className="group flex flex-col items-center gap-2 p-4 rounded-xl border border-white/10 bg-white/[0.03] hover:bg-blue-500/10 hover:border-blue-500/30 disabled:opacity-40 disabled:cursor-not-allowed transition-all duration-200"
                >
                    <BarChart3 className="w-6 h-6 text-blue-400 group-hover:scale-110 transition-transform" />
                    <span className="text-[12px] font-semibold text-gray-300 group-hover:text-white">Analyse Tour</span>
                </button>
                <button
                    onClick={() => analyzeSession()}
                    disabled={disabled}
                    className="group flex flex-col items-center gap-2 p-4 rounded-xl border border-white/10 bg-white/[0.03] hover:bg-emerald-500/10 hover:border-emerald-500/30 disabled:opacity-40 disabled:cursor-not-allowed transition-all duration-200"
                >
                    <Zap className="w-6 h-6 text-emerald-400 group-hover:scale-110 transition-transform" />
                    <span className="text-[12px] font-semibold text-gray-300 group-hover:text-white">Analyse Session</span>
                </button>
                <button
                    onClick={() => adviseSetup(selectedLapIdx ?? undefined)}
                    disabled={disabled}
                    className="group flex flex-col items-center gap-2 p-4 rounded-xl border border-white/10 bg-white/[0.03] hover:bg-amber-500/10 hover:border-amber-500/30 disabled:opacity-40 disabled:cursor-not-allowed transition-all duration-200"
                >
                    <Wrench className="w-6 h-6 text-amber-400 group-hover:scale-110 transition-transform" />
                    <span className="text-[12px] font-semibold text-gray-300 group-hover:text-white">Conseil Setup</span>
                </button>
            </div>

            {/* Loading state */}
            {isAnalyzing && (
                <div className="flex flex-col items-center justify-center py-16 gap-3">
                    <Loader2 className="w-8 h-8 text-blue-400 animate-spin" />
                    <p className="text-sm text-gray-400">Analyse en cours...</p>
                </div>
            )}

            {/* Error state */}
            {analysisError && !isAnalyzing && (
                <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 flex items-start gap-3">
                    <AlertTriangle className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
                    <div>
                        <p className="text-sm font-semibold text-red-300">Erreur</p>
                        <p className="text-[13px] text-red-400/80 mt-1">{analysisError}</p>
                    </div>
                </div>
            )}

            {/* Results */}
            {raceEngineerResult && !isAnalyzing && (
                <ResultView result={raceEngineerResult} />
            )}

            {/* Empty state */}
            {!raceEngineerResult && !isAnalyzing && !analysisError && (
                <div className="flex flex-col items-center justify-center py-16 text-center">
                    <Brain className="w-12 h-12 text-white/10 mb-4" />
                    <p className="text-sm text-gray-500">
                        Sélectionnez une analyse pour obtenir les conseils de l'ingénieur.
                    </p>
                </div>
            )}
        </div>
    );
};
