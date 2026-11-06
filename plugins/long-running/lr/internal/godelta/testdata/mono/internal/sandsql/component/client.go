package component

import "sync"

var manifestHead = sync.OnceValues(ManifestHead)

type Client struct {
	layout RowBlockLayout
}

func Open() (*Client, error) {
	head, err := manifestHead()
	if err != nil {
		return nil, err
	}
	return &Client{layout: head.Layout}, nil
}

func (c *Client) Layout() RowBlockLayout {
	return c.layout
}
