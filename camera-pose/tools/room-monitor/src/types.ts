export type Vec3 = [number, number, number];
export interface RoomConfig {
  dimensions_source: 'demo' | 'measured'; room_size: Vec3;
  bed: {size: Vec3; position: Vec3; yaw_degrees: number};
  bindings: [string, string]; installation_confirmed: boolean; same_person_confirmed: boolean;
  max_skew_ms: number; max_age_ms: number;
}
export interface CameraCalibration {camera_id: string; image_resolution: [number, number]; intrinsic_matrix: number[][]; distortion_coefficients: number[]; rotation: number[][]; translation: Vec3; reprojection_error: number}
export interface Joint3D {id: number; x: number | null; y: number | null; z: number | null; confidence: number | null; valid: boolean}
export interface Skeleton3D {personId: string; timestampMs: number; coordinateSystem: 'room_world' | 'camera_a_preview' | 'camera_a_preview_aligned' | 'bed_preview'; unit: 'm' | 'unverified_candidate_units'; joints: Joint3D[];source?:string;source_camera?:string;depth_is_model_estimate?:boolean;observation_id?:string}
export interface RoomStatus {
  sensor_crosscheck?:{status:string;camera_label?:string|null;tof_raw_label?:string|null;tof_filtered_label?:string|null;tof_confidence?:number;assisted_label?:string|null;assisted_source?:string;note?:string;maps:Record<string,{distances_mm:number[]|null;valid_zones:number}>}|null;
  camera_posture?:{method:string;status:string;label:string|null;label_ko:string;quality_score:number|null;relative_score?:number;score_is_probability:false;accuracy_verified:false;eligible_for_recording:false;accuracy_target:number;accuracy_status:string;warnings:string[];views:{camera:string;source:string;joints:number}[];labels:Record<string,string>;stable_seconds?:number};
  posture_3d?:{warnings?:string[];evidence_level?:string;required_stable_seconds?:number;status:string;label:string|null;label_ko:string|null;reason:string|null;quality_score:number|null;labels:Record<string,string>};
  experimental_preview?: {input_rotations?:number[];enabled:boolean;accuracy_verified:false;reason:string|null;cameras:CameraCalibration[];skeleton:Skeleton3D|null;alignment?:{mode?:string;source?:string;translation?:Vec3;bed_surface_height_m?:number;up_registered?:boolean;reference_info?:{source:string;pitch_degrees?:number[];up_disagreement_degrees?:number};standing_registered:boolean;lying_registered:boolean;rotation?:number[][]};bed?:{center?:Vec3;width?:number;length?:number;corners:Vec3[];coordinate_system:string;verified:boolean}|null};
  setup?:import('./CalibrationPanel').CalibrationSetup;
  captures?:string[];
  calibration_job?:{state:string;error:string|null;result:any;remaining_seconds?:number;collected_pairs?:number;metrics?:any};
  automatic_inputs?:import('./AutomaticCalibration').AutoInputs;
  automatic_saved?:boolean;
  config: RoomConfig; calibration: {cameras: CameraCalibration[]; stereo_reprojection_error: number; calibration_timestamp: string} | null;
  error: string | null; reason: string | null; skeleton: Skeleton3D | null;
  synchronization: {pairs: number; received: number[]; dropped: number[]; pair_rate_fps: number; pair_fraction: number | null; skew_ms: number | null; age_ms: number | null; frame_ids: number[] | null};
}
export interface InputSession {id: string; title: string; closed: boolean; phase: string;error?:string|null; processing_fps: number | null;processing_ms?:{read:number|null;pose:number|null;image:number|null;total:number|null};capture_diagnostics?:{mean_luma_0_255:number;low_light:boolean;highlight_clipping:boolean;diagnostic_only:true}; width: number | null; height: number | null;pose_device?:string;pose_gpu_renderer?:string;pose_max_people?:number;pose_recovery?:{visible_joints?:number;healthy?:boolean;strategy?:string;recovery_attempted?:boolean;transport_errors?:number;rotation_degrees?:number;crop_bounds?:number[]|null;person_evidence?:{confirmed:boolean;reason:string|null}|null}}
export interface View {image: string | null; width: number; height: number; age_ms: number; frame_id: number; received_ns: string; people: {edges?: number[][]; joints: {x_px: number | null; y_px: number | null; visibility?: number | null}[]}[]}
export const EDGES = [[0,1],[0,2],[1,3],[2,4],[5,6],[5,7],[7,9],[6,8],[8,10],[5,11],[6,12],[11,12],[11,13],[13,15],[12,14],[14,16]];
