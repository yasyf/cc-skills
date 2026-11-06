package uds

import "sand/proto"

type Client struct {
	*proto.RunEvent
}

func Send(event *proto.RunEvent) error {
	return nil
}
