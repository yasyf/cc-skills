package store

import "math/bits"

const (
	twisterSize   = 624
	twisterShift  = 397
	twisterMatrix = 0x9908b0df
	upperMask     = 0x80000000
	lowerMask     = 0x7fffffff
)

type pyRandom struct {
	state [twisterSize]uint32
	index int
}

func newPyRandom() *pyRandom {
	r := &pyRandom{}
	r.state[0] = 19650218
	for index := 1; index < twisterSize; index++ {
		previous := r.state[index-1]
		r.state[index] = 1812433253*(previous^previous>>30) + uint32(index)
	}
	r.index = twisterSize
	key := []uint32{0}
	i, j := 1, 0
	for k := max(twisterSize, len(key)); k > 0; k-- {
		previous := r.state[i-1]
		r.state[i] = (r.state[i] ^ (previous^previous>>30)*1664525) + key[j] + uint32(j)
		i, j = i+1, j+1
		if i >= twisterSize {
			r.state[0], i = r.state[twisterSize-1], 1
		}
		if j >= len(key) {
			j = 0
		}
	}
	for k := twisterSize - 1; k > 0; k-- {
		previous := r.state[i-1]
		r.state[i] = (r.state[i] ^ (previous^previous>>30)*1566083941) - uint32(i)
		i++
		if i >= twisterSize {
			r.state[0], i = r.state[twisterSize-1], 1
		}
	}
	r.state[0] = upperMask
	return r
}

func (r *pyRandom) next() uint32 {
	if r.index >= twisterSize {
		magnitude := [2]uint32{0, twisterMatrix}
		for k := range twisterSize {
			y := r.state[k]&upperMask | r.state[(k+1)%twisterSize]&lowerMask
			r.state[k] = r.state[(k+twisterShift)%twisterSize] ^ y>>1 ^ magnitude[y&1]
		}
		r.index = 0
	}
	y := r.state[r.index]
	r.index++
	y ^= y >> 11
	y ^= y << 7 & 0x9d2c5680
	y ^= y << 15 & 0xefc60000
	y ^= y >> 18
	return y
}

func (r *pyRandom) below(n int) int {
	width := bits.Len(uint(n))
	for {
		if value := int(r.next() >> (32 - width)); value < n {
			return value
		}
	}
}
