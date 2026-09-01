import { Claims } from './Claims';
import { Fork } from './Fork';
import { Registers } from './Registers';

export default {
  hostApi: 1,
  blocks: { registers: Registers, claims: Claims, fork: Fork },
};
