declare module "ngl" {
  export interface StructureComponent {
    addRepresentation(
      name: string,
      params?: Record<string, unknown>,
    ): { autoView: () => void };
    autoView(): void;
    dispose(): void;
  }

  export class Stage {
    constructor(container: HTMLElement, params?: Record<string, unknown>);
    loadFile(
      data: string | ArrayBuffer | Blob,
      params?: Record<string, unknown>,
    ): Promise<StructureComponent>;
    handleResize(): void;
    dispose(): void;
    autoView(): void;
  }

  const NGL: {
    Stage: typeof Stage;
    autoView: () => void;
  };
  export default NGL;
}