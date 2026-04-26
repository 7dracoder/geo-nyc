export type OptimizeMode = "geothermal" | "tunnel";

export type OptimizeRequest = {
  mode: OptimizeMode;
  params: {
    d_min: number;
    d_max: number;
    lateral_m?: number;
  };
};

export type OptimizeResponse = {
  optimal_d: number;
  objective: number;
  constraints_ok: boolean;
  diagnostics?: Record<string, unknown>;
};
