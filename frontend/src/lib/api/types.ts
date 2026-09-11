export type SessionStatus = "UPCOMING" | "LIVE" | "COMPLETED" | "DELAYED" | "UNKNOWN";
export type FreshnessState = "FRESH" | "DELAYED" | "STALE" | "UNKNOWN";
export type Availability = "LIVE_AVAILABLE" | "DELAYED_AVAILABLE" | "HISTORICAL_ONLY" | "UNAVAILABLE";

export interface Season { year: number; source: string }
export interface Constructor { id: string; name: string; nationality?: string | null }
export interface Driver { id: string; number?: number | null; code?: string | null; first_name: string; last_name: string; full_name: string }
export interface Circuit { id: string; name: string; locality?: string | null; country?: string | null }
export interface Session { type: string; name: string; date: string; start?: string | null; end?: string | null; start_local?: string; end_local?: string; source: string; provider_id?: number | null; cancelled: boolean }
export interface Event { year: number; round: number; name: string; circuit: Circuit; race_date: string; sessions: Session[]; warnings: string[] }
export interface NextSession { event: Event; session: Session; seconds_until_start: number }
export interface DriverStanding { position?: number | null; points: number; wins?: number | null; driver: Driver; constructors: Constructor[] }
export interface ConstructorStanding { position?: number | null; points: number; wins?: number | null; constructor: Constructor }
export interface NewsArticle { headline: string; source: string; url: string; published_at?: string | null; summary?: string | null }
export interface GridEntry { position?: number | null; driver: Driver; constructor?: Constructor | null; pit_lane?: boolean | null; source: string }
export interface QualifyingResult { position?: number | null; driver: Driver; constructor: Constructor; q1?: string | null; q2?: string | null; q3?: string | null }
export interface RaceResult { position?: number | null; position_text?: string | null; driver: Driver; constructor: Constructor; grid?: number | null; points?: number | null; laps?: number | null; status?: string | null; time?: string | null }
export interface ProviderStatus { provider: string; status: "not_checked" | "ok" | "degraded" }
export interface SessionState { session: Session; status: SessionStatus }

export interface WeekendState {
  season?: number | null; event?: Event | null; circuit?: string | null; country?: string | null;
  round?: number | null; weekend_format: "STANDARD" | "SPRINT" | "UNKNOWN"; status: SessionStatus;
  session_schedule: SessionState[]; completed_sessions: Session[]; active_session?: Session | null;
  next_session?: NextSession | null; qualifying: QualifyingResult[]; grid: GridEntry[];
  latest_results: RaceResult[]; driver_standings: DriverStanding[];
  constructor_standings: ConstructorStanding[]; weather_available: boolean;
  news_summary: NewsArticle[]; provider_status: ProviderStatus[]; errors: Record<string, string>;
}
export interface Home {
  active_season?: { year: number } | null; current_or_next_event?: Event | null; weekend_status: string;
  next_session?: NextSession | null; weekend_schedule: Session[]; grid_event?: Event | null;
  recent_or_available_grid: GridEntry[]; driver_standings_top: DriverStanding[];
  constructor_standings_top: ConstructorStanding[]; latest_news: NewsArticle[];
  provider_status: ProviderStatus[]; errors: Record<string, string>; current_weekend?: WeekendState | null;
  live_status?: LiveStatus | null;
}
export interface Freshness { source: string; retrieved_at: string; retrieved_at_local?: string; provider_timestamp?: string | null; provider_timestamp_local?: string; data_age_seconds?: number | null; state: FreshnessState }
export interface LiveStatus { live: boolean; availability: Availability; session_status: SessionStatus; event?: Event | null; session?: Session | null; next_session?: NextSession | null; provider: string; freshness: Freshness; reason?: string | null }
export interface DataQuality { confidence: string; fields: Record<string, string>; warnings: string[] }
export interface DriverRaceState { driver: Driver; constructor?: Constructor | null; position?: number | null; grid_position?: number | null; laps_completed?: number | null; gap_to_leader?: number | null; gap_to_ahead?: number | null; gap_to_behind?: number | null; gap_observed_at?: number | null; interval_observed_at?: number | null; compound?: string | null; tyre_age?: number | null; tyre_age_observed_at?: number | null; stint_number?: number | null; pit_stops_completed: number; last_lap_time?: number | null; last_lap_number?: number | null; recent_clean_pace?: number | null; pace_laps?: number[]; status: string; active?: boolean | null; retired?: boolean | null; lapped?: boolean | null; laps_behind?: number | null; quality: DataQuality }
export interface ReplayAvailableLaps { year: number; round: number; laps: number[]; participants: number; definition: string }
export interface ReplayTrackState { track_status?: string | null; safety_car?: boolean | null; virtual_safety_car?: boolean | null; red_flag?: boolean | null }
export interface ReplayRaceState { event: Event; session: Session; current_lap: number; total_scheduled_laps?: number | null; session_time_seconds: number; elapsed_race_seconds: number; timestamp?: string | null; drivers: DriverRaceState[]; track: ReplayTrackState; quality: DataQuality; source: string }
export type AnalysisConfidence = "HIGH" | "MEDIUM" | "LOW" | "INSUFFICIENT";
export type TrafficLevel = "CLEAR_AIR" | "LIGHT_TRAFFIC" | "MODERATE_TRAFFIC" | "HEAVY_TRAFFIC" | "UNKNOWN";
export interface AnalysisEvidence { confidence: AnalysisConfidence; method: string; components: Record<string, unknown>; warnings: string[] }
export interface PaceAnalysis extends AnalysisEvidence { seconds?: number | null; lap_numbers: number[]; sample_count: number }
export interface TyreAnalysis extends AnalysisEvidence { driver_id: string; compound?: string | null; stint_number?: number | null; tyre_age?: number | null; reference_coverage?: number | null; sample_count: number }
export interface PitLossAnalysis extends AnalysisEvidence { total_seconds?: number | null; sample_count: number }
export interface RejoinAnalysis extends AnalysisEvidence { driver_id: string; projected_position?: number | null; position_range?: [number, number] | null; gap_ahead?: number | null; gap_behind?: number | null; ahead_id?: string | null; behind_id?: string | null; nearby_drivers: string[]; traffic: TrafficLevel }
export interface TrafficAnalysis extends AnalysisEvidence { driver_id: string; status: TrafficLevel; ahead_id?: string | null; behind_id?: string | null; gap_ahead?: number | null; gap_behind?: number | null; relative_pace_ahead?: number | null; relative_pace_behind?: number | null; clear_air?: boolean | null; slower_car_blockage?: boolean | null; rejoin_density?: number | null }
export interface PitWindowAnalysis extends AnalysisEvidence { current_lap: number; rejoin: RejoinAnalysis; clear_air_opportunity?: boolean | null; traffic_risk: TrafficLevel }
export interface DriverAnalysis { year: number; round: number; lap: number; cutoff_seconds: number; driver: DriverRaceState; recent_pace: PaceAnalysis; tyres: TyreAnalysis; pit_loss: PitLossAnalysis; rejoin: RejoinAnalysis; traffic: TrafficAnalysis; pit_window: PitWindowAnalysis }
export interface PairAnalysis extends AnalysisEvidence { kind: "undercut" | "overcut"; driver_id: string; target_id: string; current_gap?: number | null; required_gain?: number | null; estimated_fresh_tyre_gain?: number | null; traffic_penalty?: number | null; estimated_margin?: number | null; opportunity: "YES" | "MARGINAL" | "NO" | "UNKNOWN"; conditions_required: string[] }
export interface WeatherState { air_temperature_c?: number | null; track_temperature_c?: number | null; humidity_percent?: number | null; rainfall?: number | boolean | null; wind_speed?: number | null; wind_direction_degrees?: number | null; observed_at?: string | null }
export interface LiveRace { live: boolean; availability: Availability; event?: Event | null; session?: Session | null; lap?: number | null; status: SessionStatus; track_status: string; drivers: DriverRaceState[]; provider: string; freshness: Freshness; next_session?: NextSession | null; next_event?: Event | null; current_weekend_status: SessionStatus; latest_results: RaceResult[]; news: NewsArticle[]; missing_requirements: string[]; weather?: WeatherState | null }
export interface RaceControlMessage { timestamp?: string | null; category?: string | null; flag?: string | null; scope?: string | null; message?: string | null }
export interface RaceControlResponse { available: boolean; track_status: string; messages: RaceControlMessage[]; freshness: Freshness; reason?: string | null }
export interface WeatherResponse { available: boolean; weather?: WeatherState | null; freshness: Freshness; reason?: string | null }
export interface PitWallAlert { kind: string; driver_id: string; rival_id?: string | null; detail: string }
export interface PitWallRival { driver: Driver; position?: number | null; gap_seconds?: number | null; relative_pace_seconds_per_lap?: number | null; pit_stops_completed: number; status: string }
export interface PairedOutcome { horizon_laps: 1 | 3 | 5; median_time_delta_seconds?: number | null; interval_80?: [number, number] | null; pit_net_position_range_80?: [number, number] | null; extend_net_position_range_80?: [number, number] | null }
export interface PairedComparison { pit_action: string; extend_action: string; median_time_delta_seconds?: number | null; pit_net_position_range_80?: [number, number] | null; extend_net_position_range_80?: [number, number] | null; pit_window_state: string; outcomes: PairedOutcome[] }
export interface PitWallDriver { driver: Driver; status: string; current_position?: number | null; gap_kind: "TIME" | "LAP_DEFICIT" | "UNKNOWN"; gap_to_leader_seconds?: number | null; laps_behind?: number | null; compound?: string | null; tyre_age?: number | null; recent_pace_seconds_per_lap?: number | null; traffic: string; pit_cycle_position?: number | null; recommendation?: string | null; alternative?: string | null; decision_state: string; model_disagreement: boolean; relevant_rivals: PitWallRival[]; alerts: PitWallAlert[]; paired_comparison?: PairedComparison | null; main_risk?: string | null; main_opportunity?: string | null; data_quality: Record<string, unknown> }
export interface PitWallSnapshot { lap: number; race_status: string; trajectory_count: number; drivers: PitWallDriver[]; alerts: PitWallAlert[] }
export interface CurrentPitWall { analysis_status: "AVAILABLE" | "PARTIAL" | "UNAVAILABLE"; strategy_status: "EXPERIMENTAL"; provider: string; last_update?: string | null; data_freshness: Freshness; snapshot?: PitWallSnapshot | null; driver?: PitWallDriver | null; current_state?: DriverRaceState | null; missing_requirements: string[] }
export interface NewsFeed { articles: NewsArticle[]; provider_status: ProviderStatus[] }
